"""CLI entry point for DownV."""

import os
import re
import sys
import termios
import tty
from pathlib import Path
from typing import Dict

from yt_dlp.utils import sanitize_filename

from downv import history
from downv import __version__
from downv.downloader import (
    DownloadFailure,
    download_media,
    ffmpeg_available,
    find_existing_download,
)
from downv.extractor import MediaInfoError, get_media_info
from downv.formats import (
    SelectedMediaFormat,
    format_size,
    pick_quality_by_height,
    select_formats,
)
from downv.paths import OutputDirectoryError, get_output_directory, resolve_output_directory


class _MenuCancelled(Exception):
    """Raised when an interactive menu is cancelled (EOF / non-TTY stdin).

    Distinguishes an explicit user-cancelled quality choice from the normal
    ``None`` that simply means "no menu was displayed".
    """


def _read_line(prompt: str) -> str | None:
    """Read one line of input, returning the raw (untrimmed) string.

    Returns ``None`` when stdin is exhausted (EOF) so callers can terminate
    cleanly instead of looping forever or crashing with an ``EOFError``.
    """
    try:
        return input(prompt)
    except EOFError:
        return None


def _menu_cancelled() -> None:
    print()
    print("Download cancelled.")


def _clear_menu(rendered_lines: int) -> None:
    """Erase the lines last drawn by the quality menu so cancellation leaves the
    terminal clean instead of a dangling menu."""
    if rendered_lines <= 0:
        return
    moves = rendered_lines - 1
    sys.stdout.write(f"\033[{moves}A")
    for i in range(rendered_lines):
        sys.stdout.write("\033[2K")
        if i < rendered_lines - 1:
            sys.stdout.write("\n")
    sys.stdout.write(f"\033[{moves}A")
    sys.stdout.flush()


def prompt_for_url() -> str | None:
    while True:
        line = _read_line("Enter URL: ")
        if line is None:
            return None
        url = line.strip()
        if url:
            return url
        print()
        print("Please enter a URL.")
        print()


def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def display_info(info: dict) -> None:
    print("✓ Media information retrieved")
    print()

    title = info.get("title", "Unknown")
    uploader = info.get("uploader") or info.get("channel") or "Unknown"
    duration = info.get("duration")
    duration_str = format_duration(duration) if duration else "Unknown"

    print(f"Title    : {title}")
    print(f"Uploader : {uploader}")
    print(f"Duration : {duration_str}")

    chapters = info.get("chapters") or []
    if chapters:
        print(f"Chapters : {len(chapters)} chapters detected")
    else:
        print("Chapters : None")


def read_key() -> str:
    """Read a single key from a raw terminal, or ``"EOF"`` when unavailable.

    ``"EOF"`` is returned when:
      * stdin is not an interactive terminal (raw mode cannot be entered), or
      * the underlying read returns no data (end of stream).

    This keeps the quality menu from calling ``termios``/``tty`` on a non-TTY
    and from looping forever when the stream is exhausted.
    """
    if not sys.stdin.isatty():
        return "EOF"
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if not ch:
            return "EOF"
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "UP"
            if seq == "[B":
                return "DOWN"
            if seq == "[C":
                return "RIGHT"
            if seq == "[D":
                return "LEFT"
            return "ESC"
        if ch in ("\r", "\n"):
            return "ENTER"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select_quality(qualities: Dict[int, SelectedMediaFormat]) -> SelectedMediaFormat | None:
    items = list(qualities.items())
    index = 0
    rendered_lines = 0

    def render() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            moves = rendered_lines - 1
            sys.stdout.write(f"\033[{moves}A")
            for i in range(rendered_lines):
                sys.stdout.write("\033[2K")
                if i < rendered_lines - 1:
                    sys.stdout.write("\n")
            sys.stdout.write(f"\033[{moves}A")
        lines = ["Available qualities:", ""]
        for i, (height, entry) in enumerate(items):
            size = format_size(entry.size_bytes)
            marker = "❯" if i == index else " "
            lines.append(f"{marker} {entry.label} — {size}")
        lines.append("")
        lines.append("Use ↑/↓ to navigate, Enter to select.")
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()
        rendered_lines = len(lines)

    while True:
        try:
            render()
            key = read_key()
        except KeyboardInterrupt:
            _clear_menu(rendered_lines)
            raise
        if key in ("EOF", ""):
            _clear_menu(rendered_lines)
            return None
        if key == "UP":
            index = (index - 1) % len(items)
        elif key == "DOWN":
            index = (index + 1) % len(items)
        elif key == "ENTER":
            selected = items[index][1]
            print()
            print(f"Selected: {selected.label}")
            return selected


def clear_history() -> None:
    """Clear all download history metadata (never touches media files)."""
    print("DownV - Download History")
    print()
    try:
        # Read through the API first so a corrupted history file is detected
        # rather than silently overwritten/truncated by the clear operation.
        history.get_download_history()
        history.clear_history()
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    print("✓ Download history cleared.")


def show_history_count() -> None:
    """Print the number of recorded downloads (metadata only, read-only)."""
    print("DownV - Download History")
    print()
    try:
        count = history.count_history()
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    print(f"Total downloads: {count}")


def remove_history(video_id: str) -> None:
    """Remove the history metadata record for a video (never the media file)."""
    print("DownV - Download History")
    print()
    try:
        removed = history.remove_download(video_id)
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    if removed is None:
        print(f"No record found for video ID: {video_id}")
        return

    print(f"✓ Removed download record for video ID: {video_id}")


def _file_status(record: dict) -> str:
    """Return a read-only status for the record's media file (existence only)."""
    filepath = record.get("filepath")
    if not filepath:
        return "✗ File missing"
    try:
        exists = Path(filepath).is_file()
    except (OSError, ValueError, TypeError):
        return "✗ File missing"
    return "✓ File exists" if exists else "✗ File missing"


def _print_existing(info: dict) -> None:
    """Print the most recent matching history record for an already-downloaded video."""
    print("✓ Video already downloaded")
    video_id = info.get("id")
    if not video_id:
        return
    try:
        records = history.find_downloads(video_id)
    except history.HistoryError as exc:
        print()
        print(f"Warning: {exc}")
        return
    if not records:
        return
    record = records[-1]
    title = record.get("title", "Unknown")
    quality = record.get("quality")
    quality_str = f"{quality}p" if quality else "n/a"
    filepath = record.get("filepath", "?")
    print(f"  Title    : {title}")
    print(f"  Quality  : {quality_str}")
    print(f"  File     : {filepath}")
    print(f"  Status   : {_file_status(record)}")


def show_history_detail(video_id: str) -> None:
    """Print the full metadata for a single recorded download."""
    print("DownV - Download History")
    print()
    try:
        record = history.find_download(video_id)
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    if record is None:
        print(f"No record found for video ID: {video_id}")
        return

    title = record.get("title", "Unknown")
    quality = record.get("quality")
    quality_str = f"{quality}p" if quality else "n/a"

    print(f"Title       : {title}")
    print(f"Video ID    : {record.get('video_id', '?')}")
    print(f"Quality     : {quality_str}")
    duration = record.get("duration")
    print(f"Duration    : {format_duration(duration) if duration else 'Unknown'}")
    print(f"Filename    : {record.get('filename', '?')}")
    print(f"Filepath    : {record.get('filepath', '?')}")
    print(f"Status      : {_file_status(record)}")
    print(f"Downloaded  : {record.get('downloaded_at', 'unknown')}")


def search_history(query: str) -> None:
    """Print history records matching ``query`` (title or video_id), newest first."""
    print("DownV - Download History")
    print()
    try:
        records = history.get_download_history()
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    needle = query.strip().lower()
    matches = [
        record
        for record in records
        if needle in (record.get("title") or "").lower()
        or needle in (record.get("video_id") or "").lower()
    ]

    if not matches:
        print("No matching downloads.")
        return

    for record in matches:
        title = record.get("title", "Unknown")
        video_id = record.get("video_id", "?")
        quality = record.get("quality")
        quality_str = f"{quality}p" if quality else "n/a"
        downloaded_at = record.get("downloaded_at", "unknown")
        print(f"- {title}")
        print(f"  Video ID   : {video_id}")
        print(f"  Quality    : {quality_str}")
        print(f"  Status     : {_file_status(record)}")
        print(f"  Downloaded : {downloaded_at}")

    print()
    print(f"Total: {len(matches)}")


def show_history() -> None:
    """Print the download history (metadata only), newest first."""
    print("DownV - Download History")
    print()
    try:
        records = history.get_download_history()
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    if not records:
        print("No downloads recorded yet.")
        return

    for record in records:
        title = record.get("title", "Unknown")
        video_id = record.get("video_id", "?")
        quality = record.get("quality")
        quality_str = f"{quality}p" if quality else "n/a"
        downloaded_at = record.get("downloaded_at", "unknown")
        print(f"- {title}")
        print(f"  Video ID   : {video_id}")
        print(f"  Quality    : {quality_str}")
        print(f"  Status     : {_file_status(record)}")
        print(f"  Downloaded : {downloaded_at}")

    print()
    print(f"Total: {len(records)}")


def _safe_playlist_count(info: dict):
    """Best-effort count of a playlist's entries without forcing resolution.

    Prefers the authoritative ``playlist_count`` field, which yt-dlp reports
    without resolving every entry. Falls back to ``len(entries)`` only when
    ``entries`` is a concrete, sized sequence. Returns None when the count
    cannot be determined safely (e.g. ``entries`` is a lazy iterator).
    """
    count = info.get("playlist_count")
    if isinstance(count, int) and count >= 0:
        return count
    entries = info.get("entries")
    if entries is not None:
        try:
            return len(entries)
        except (TypeError, AttributeError):
            return None
    return None


def _describe_playlist(info: dict) -> tuple:
    """Return (title, uploader, count) for a playlist, tolerating missing data."""
    title = (
        info.get("title")
        or info.get("playlist_title")
        or info.get("id")
        or "Untitled playlist"
    )
    uploader = (
        info.get("uploader")
        or info.get("channel")
        or info.get("playlist_uploader")
        or "Unknown"
    )
    count = _safe_playlist_count(info)
    return title, uploader, count


def _handle_playlist(info: dict) -> bool:
    """Display playlist metadata and ask for confirmation.

    Returns True if the user confirmed downloading, False otherwise. No media
    is downloaded here; this only stages the decision for a later step.
    """
    title, uploader, count = _describe_playlist(info)

    print("Playlist detected")
    print()
    print(f"  Title    : {title}")
    print(f"  Uploader : {uploader}")
    if count is not None:
        print(f"  Videos   : {count}")
        prompt = f"\nDownload all {count} videos? [y/N]: "
    else:
        print("  Videos   : ?")
        prompt = "\nDownload all videos? [y/N]: "

    answer = _read_line(prompt)
    if answer is None:
        return False
    return answer.strip().lower() in ("y", "yes")


def _ask_preserve_chapters(prompt: str) -> bool:
    """Ask the user whether to preserve chapters, returning True/False.

    Follows the existing yes/no convention used by ``_handle_playlist``:
    ``y``/``yes`` (any case) means True; anything else (``n``, empty, or EOF)
    means False. A defunct/EOF stdin yields False with no traceback.
    """
    answer = _read_line(prompt)
    if answer is None:
        return False
    return answer.strip().lower() in ("y", "yes")


def _commit_download(
    info: dict,
    selected: SelectedMediaFormat,
    output_dir: Path,
    failed_reason: list | None = None,
    verbose: bool = False,
    preserve_chapters: bool = False,
) -> str:
    """Run duplicate detection + FFmpeg check + media download for a resolved
    item using a concrete format and output directory.

    Shared by standalone and playlist item downloads. Returns the outcome:
    ``"skipped"`` (already downloaded), ``"failed"``, or ``"downloaded"``.

    When ``failed_reason`` is provided, a concise failure reason is appended to
    it for each ``"failed"`` outcome so callers can report the reason without
    capturing stdout. Standalone callers leave it None and are unaffected.
    """
    existing = find_existing_download(info)
    if existing:
        print()
        _print_existing(info)
        return "skipped"

    if selected.needs_merge and not ffmpeg_available():
        print()
        print("✗ FFmpeg is required to download this quality.")
        print()
        print("Please install FFmpeg and try again.")
        if failed_reason is not None:
            failed_reason.append("FFmpeg is required to download this quality.")
        return "failed"

    print()
    print("Starting download...")
    print()
    try:
        if preserve_chapters:
            download_media(info, selected, output_dir, preserve_chapters=True)
        else:
            download_media(info, selected, output_dir)
    except DownloadFailure as exc:
        print("✗ Download failed.")
        print()
        print(f"Reason: {exc}")
        _debug(f"Download error: {exc}", verbose)
        if failed_reason is not None:
            failed_reason.append(str(exc).strip() or "Download failed.")
        return "failed"

    print()
    print("✓ Download completed")
    return "downloaded"


def _download_video(
    info: dict,
    selected: SelectedMediaFormat | None = None,
    output_dir: Path | None = None,
    failed_reason: list | None = None,
    verbose: bool = False,
    preserve_chapters: bool = False,
    quality: int | None = None,
) -> str:
    """Process a single video through the shared download pipeline.

    If ``selected`` is provided (playlist items using a preselected playlist
    quality), the per-video quality picker is skipped. If ``output_dir`` is
    provided (playlist directory), it is used instead of the default output
    directory. Standalone calls keep their existing interactive behaviour.

    When ``quality`` is provided (non-interactive ``--quality``), the requested
    height is picked from the available formats automatically without showing
    the interactive menu.

    Returns the outcome so playlist orchestration can tally results:
    ``"downloaded"`` when a new download completed, ``"skipped"`` when
    duplicate detection found a valid prior download, or ``"failed"`` when
    the item could not be downloaded for any reason.

    When ``failed_reason`` is provided, a concise reason is appended to it on a
    ``"failed"`` outcome; standalone callers leave it None and are unaffected.
    """
    display_info(info)

    if selected is None:
        qualities = select_formats(info)
        if not qualities:
            print("No video formats available.")
            if failed_reason is not None:
                failed_reason.append("No video formats available.")
            return "failed"
        if quality is not None:
            selected = pick_quality_by_height(qualities, quality)
            if selected is None:
                print("No video formats available.")
                if failed_reason is not None:
                    failed_reason.append("No video formats available.")
                return "failed"
        else:
            print()
            selected = select_quality(qualities)
            if selected is None:
                _menu_cancelled()
                if failed_reason is not None:
                    failed_reason.append("Download cancelled.")
                return "failed"

    if output_dir is None:
        env_dir = os.environ.get("DOWNV_OUTPUT_DIR")
        output_dir = (Path(env_dir).expanduser() if env_dir else get_output_directory())
        was_missing = not output_dir.exists()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print("✗ Could not create output directory.")
            print()
            print(f"Reason: {exc}")
            if failed_reason is not None:
                failed_reason.append(str(exc).strip() or "Could not create output directory.")
            return "failed"
        if was_missing:
            print(f"✓ Created output directory: {output_dir}/")
        else:
            print(f"✓ Output directory ready: {output_dir}/")

    if verbose and selected is not None:
        _debug(f"Selected quality: {selected.height}p", verbose)
    return _commit_download(info, selected, output_dir, failed_reason, verbose, preserve_chapters)


def _playlist_entry_url(entry: dict) -> str | None:
    return (
        entry.get("webpage_url")
        or entry.get("original_url")
        or entry.get("url")
    )


def _resolve_playlist_entry(entry) -> dict | None:
    """Return a fully-resolved single-video info dict for a playlist entry.

    Fully-resolved entries (those already carrying ``formats``) are returned
    unchanged. Partial entries are resolved through the extractor using their
    URL. Returns None when the entry is unusable or could not be resolved.
    """
    if not isinstance(entry, dict):
        return None
    if "formats" in entry:
        # A resolved entry is only useful to the download pipeline when it has a
        # usable download URL (``webpage_url``/``original_url``). Some entries
        # (notably extractor output) carry only a plain ``url``; forward that so
        # the downloader is not left without a URL. Returns a shallow copy only
        # when normalizing, to avoid mutating the caller's dict.
        if not (entry.get("webpage_url") or entry.get("original_url")):
            url = entry.get("url")
            if not url:
                return None
            resolved = dict(entry)
            resolved["original_url"] = resolved.get("original_url") or url
            resolved["webpage_url"] = resolved.get("webpage_url") or url
            return resolved
        return entry
    url = _playlist_entry_url(entry)
    if not url:
        return None
    try:
        return get_media_info(url)
    except MediaInfoError:
        return None


def _print_playlist_summary(stats: dict) -> None:
    """Print the final playlist summary (Total/Downloaded/Skipped/Failed/Unresolved)."""
    print()
    print("Playlist complete")
    print()
    print(f"  Total      : {stats['total']}")
    print(f"  Downloaded : {stats['downloaded']}")
    print(f"  Skipped    : {stats['skipped']}")
    print(f"  Failed     : {stats['failed']}")
    print(f"  Unresolved : {stats['unresolved']}")


def _print_item_report(header: str, items: list, marker: str) -> None:
    """Print an item report section (e.g. Failed/Skipped/Unresolved items).

    ``items`` is a list of ``{"title": str, "reason": str}`` dicts in playlist
    order. The section is only printed when there is at least one item.
    """
    if not items:
        return
    print()
    print(header)
    for item in items:
        print(f"  {marker} {item['title']}")
        print(f"    Reason: {item['reason']}")


_MAX_PLAYLIST_DIR_NAME = 120


def _item_title(resolved, entry) -> str:
    """Return a usable title for a playlist item, falling back safely."""
    for source in (resolved, entry):
        if isinstance(source, dict):
            title = source.get("title")
            if isinstance(title, str) and title.strip():
                return title
    return "Unknown title"


def playlist_dir_name(title: str) -> str:
    """Return a safe, filesystem-friendly name for a playlist directory.

    Strips invalid characters, collapses whitespace, trims surrounding dots and
    whitespace, and truncates to a sane maximum. Falls back to ``"Playlist"``
    when nothing usable remains. The original ``title`` is never mutated.
    """
    base = sanitize_filename(title or "", restricted=False) or ""
    base = base.strip().rstrip(".").replace(" ", "_")
    base = re.sub(r"__+", "_", base)
    if not base:
        return "Playlist"
    return base[:_MAX_PLAYLIST_DIR_NAME].rstrip("._")


def _playlist_output_dir(title: str, base: Path | None = None) -> Path:
    """Return the dedicated subdirectory for a playlist, creating it if needed.

    Lives under the base output directory so standalone videos are unaffected:
    ``<base>/<Safe Playlist Title>/``. When ``base`` is None the default output
    directory is used.
    """
    base_dir = base if base is not None else get_output_directory()
    target = base_dir / playlist_dir_name(title)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputDirectoryError(f"{exc.strerror or exc} ({exc.filename or target})") from exc
    return target


def _aggregate_playlist_qualities(per_item: list) -> Dict[int, SelectedMediaFormat]:
    """Build a playlist-wide quality menu map (one entry per height).

    Consumes the precomputed per-item quality maps from :func:`_plan_playlist`
    (``per_item``), so ``select_formats`` is computed once per item rather than
    again here. Each returned :class:`SelectedMediaFormat` carries only the
    aggregated estimated total size across all resolvable items; format IDs stay
    blank because the real per-item IDs are looked up from ``per_item`` during
    download. If any contributing item has an unknown size, that height's total
    is reported as unknown rather than silently under-reporting.
    """
    totals: Dict[int, SelectedMediaFormat] = {}
    for item in per_item:
        if not item.get("resolved"):
            continue
        for height, fmt in item["qualities"].items():
            existing = totals.get(height)
            if existing is None:
                totals[height] = SelectedMediaFormat(
                    height, "", None, fmt.size_bytes
                )
                continue
            if existing.size_bytes is None or fmt.size_bytes is None:
                totals[height] = SelectedMediaFormat(height, "", None, None)
            else:
                totals[height] = SelectedMediaFormat(
                    height, "", None, existing.size_bytes + fmt.size_bytes
                )
    return totals


def _choose_playlist_quality(per_item: list) -> int | None:
    """Show a single quality menu for the whole playlist and return the chosen height.

    The menu totals reflect every playlist item, not just the first video. If no
    resolvable item yields any quality, returns None (no menu displayed).
    """
    menu = _aggregate_playlist_qualities(per_item)
    if not menu:
        return None
    print()
    print("Playlist quality")
    selected = select_quality(menu)
    if selected is None:
        raise _MenuCancelled
    return selected.height


def _plan_playlist(info: dict, quality: int | None = None) -> tuple:
    """Resolve every entry once and compute its per-item quality map.

    Each entry is resolved exactly once and its ``select_formats`` map is
    computed exactly once. Returns ``(chosen_height_or_None, per_item)`` where
    ``per_item`` is a list parallel to ``info["entries"]``; each element is a
    dict with ``"resolved"`` (an info dict or None) and ``"qualities"`` (the
    ``{height: SelectedMediaFormat}`` map, empty for unresolvable items). The
    same ``per_item`` list is reused for both menu aggregation and download, so
    entries and formats are never materialised twice.

    When ``quality`` is provided (``--quality``), no interactive menu is shown;
    the requested height is used directly as the chosen quality.
    """
    per_item = []
    for entry in info.get("entries") or []:
        resolved = _resolve_playlist_entry(entry)
        qualities = select_formats(resolved) if resolved else {}
        per_item.append({"resolved": resolved, "qualities": qualities})
    if quality is not None:
        return quality, per_item
    chosen_height = _choose_playlist_quality(per_item)
    return chosen_height, per_item


def _build_playlist_items(info: dict, per_item: list | None = None) -> list:
    """Build item descriptors (``index``, ``entry``, ``resolved``, ``qualities``).

    The descriptors are shared by the initial playlist run and any retry runs so
    both reuse the same processing logic. When ``per_item`` is provided (from
    the planning phase), the already-resolved entry info and its precomputed
    quality map are carried over so neither entries nor ``select_formats`` are
    recomputed. Otherwise ``resolved``/``qualities`` start as None and are
    resolved lazily during processing.
    """
    entries = info.get("entries") or []
    items = []
    for index, entry in enumerate(entries):
        if per_item is not None:
            items.append(
                {
                    "index": index,
                    "entry": entry,
                    "resolved": per_item[index]["resolved"],
                    "qualities": per_item[index]["qualities"],
                }
            )
        else:
            items.append({"index": index, "entry": entry, "resolved": None, "qualities": None})
    return items


def _process_items(
    items: list,
    chosen_height: int | None,
    playlist_dir: Path | None,
    label: str,
    known_count: int | None = None,
    verbose: bool = False,
    preserve_chapters: bool = False,
    quality_fallback: bool = False,
) -> tuple:
    """Process a collection of playlist item descriptors through the shared pipeline.

    ``items`` is a list of :func:`_build_playlist_items` descriptors. Each item
    is classified into exactly one category (downloaded/skipped/failed/
    unresolved) and this helper returns ``(stats, failed, skipped, unresolved)``
    where ``stats`` holds the tallies and the three category lists carry the
    report dicts plus the retry metadata (``index``, ``entry``, ``resolved``,
    ``qualities``). The same helper powers the initial run (``label``
    ``"Playlist item"``) and each retry run (``label`` ``"Retry item"``); for
    retry, items whose ``resolved`` is None are resolved again so previously
    unresolvable items get another chance.

    ``known_count`` is the display total used for the per-item progress
    denominator (``Playlist item N/known_count``). The initial run passes the
    playlist count, which may be unknown/None so no denominator is fabricated;
    a retry passes the size of the retry subset so progress is shown relative
    to it.

    When ``quality_fallback`` is True (non-interactive ``--quality``), an item
    that lacks the exact requested height falls back to the closest available
    height at or below it. When False (interactive playlist selection), an item
    missing the exact chosen height is marked failed as before.
    """
    stats = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "unresolved": 0}
    failed_items = []
    skipped_items = []
    unresolved_items = []
    total = known_count if known_count else None
    try:
        for number, item in enumerate(items, start=1):
            entry = item["entry"]
            resolved = item.get("resolved")
            qualities = item.get("qualities")
            denominator = f"/{total}" if total else ""
            print(f"{label} {number}{denominator}")
            entry_title = entry.get("title", "Unknown") if isinstance(entry, dict) else "Unknown"
            print(f"  Title : {entry_title}")
            if resolved is None:
                resolved = _resolve_playlist_entry(entry)
            if resolved is None:
                stats["total"] += 1
                stats["unresolved"] += 1
                print()
                print(f"✗ Could not resolve playlist item {number}.")
                print()
                unresolved_items.append(
                    {
                        "index": item["index"],
                        "entry": entry,
                        "resolved": None,
                        "qualities": None,
                        "title": _item_title(None, entry),
                        "reason": "Could not resolve video information.",
                    }
                )
                continue
            print()
            failed_reason = []
            try:
                if chosen_height is not None:
                    if qualities is None:
                        qualities = select_formats(resolved)
                    if quality_fallback:
                        selected = pick_quality_by_height(qualities, chosen_height)
                    else:
                        selected = qualities.get(chosen_height)
                    if selected is None:
                        stats["total"] += 1
                        stats["failed"] += 1
                        print(f"  Quality {chosen_height}p not available for this item.")
                        print()
                        failed_items.append(
                            {
                                "index": item["index"],
                                "entry": entry,
                                "resolved": resolved,
                                "qualities": qualities,
                                "title": _item_title(resolved, entry),
                                "reason": "Selected quality not available.",
                            }
                        )
                        continue
                    if verbose:
                        if preserve_chapters:
                            outcome = _download_video(
                                resolved, selected=selected, output_dir=playlist_dir,
                                failed_reason=failed_reason, verbose=verbose,
                                preserve_chapters=preserve_chapters,
                            )
                        else:
                            outcome = _download_video(
                                resolved, selected=selected, output_dir=playlist_dir,
                                failed_reason=failed_reason, verbose=verbose,
                            )
                    else:
                        if preserve_chapters:
                            outcome = _download_video(
                                resolved, selected=selected, output_dir=playlist_dir,
                                failed_reason=failed_reason,
                                preserve_chapters=preserve_chapters,
                            )
                        else:
                            outcome = _download_video(
                                resolved, selected=selected, output_dir=playlist_dir,
                                failed_reason=failed_reason,
                            )
                    if outcome == "failed":
                        reason = failed_reason[0].strip() if failed_reason else "Download failed."
                        failed_items.append(
                            {
                                "index": item["index"],
                                "entry": entry,
                                "resolved": resolved,
                                "qualities": qualities,
                                "title": _item_title(resolved, entry),
                                "reason": reason,
                            }
                        )
                    elif outcome == "skipped":
                        skipped_items.append(
                            {
                                "index": item["index"],
                                "entry": entry,
                                "resolved": resolved,
                                "qualities": qualities,
                                "title": _item_title(resolved, entry),
                                "reason": "Already downloaded.",
                            }
                        )
                    stats["total"] += 1
                    stats[outcome] += 1
                else:
                    if verbose:
                        if preserve_chapters:
                            outcome = _download_video(resolved, verbose=verbose, preserve_chapters=preserve_chapters)
                        else:
                            outcome = _download_video(resolved, verbose=verbose)
                    else:
                        if preserve_chapters:
                            outcome = _download_video(resolved, preserve_chapters=preserve_chapters)
                        else:
                            outcome = _download_video(resolved)
                    if outcome == "failed":
                        failed_items.append(
                            {
                                "index": item["index"],
                                "entry": entry,
                                "resolved": resolved,
                                "qualities": qualities,
                                "title": _item_title(resolved, entry),
                                "reason": "Download failed.",
                            }
                        )
                    elif outcome == "skipped":
                        skipped_items.append(
                            {
                                "index": item["index"],
                                "entry": entry,
                                "resolved": resolved,
                                "qualities": qualities,
                                "title": _item_title(resolved, entry),
                                "reason": "Already downloaded.",
                            }
                        )
                    stats["total"] += 1
                    stats[outcome] += 1
            except Exception as exc:
                stats["total"] += 1
                stats["failed"] += 1
                print(f"✗ Failed to download playlist item {number}.")
                print()
                print(f"Reason: {exc}")
                failed_items.append(
                    {
                        "index": item["index"],
                        "entry": entry,
                        "resolved": resolved,
                        "qualities": qualities,
                        "title": _item_title(resolved, entry),
                        "reason": str(exc).strip() or "Download failed.",
                    }
                )
    except KeyboardInterrupt:
        if stats["total"]:
            print()
            _report_playlist_complete(stats, failed_items, skipped_items, unresolved_items)
        raise

    return stats, failed_items, skipped_items, unresolved_items


def _report_playlist_complete(stats: dict, failed: list, skipped: list, unresolved: list) -> None:
    """Print the playlist summary followed by the skipped/unresolved/failed report sections."""
    _print_playlist_summary(stats)
    _print_item_report("Skipped items:", skipped, "-")
    _print_item_report("Unresolved items:", unresolved, "?")
    _print_item_report("Failed items:", failed, "✗")


def _print_retry_summary(stats: dict) -> None:
    """Print the summary shown after a retry run of a playlist subset.

    The counts deliberately describe only the current retry round, not the whole
    playlist, so the scope is stated in a subheading to avoid misreading them as
    the overall playlist totals.
    """
    print()
    print("Retry complete")
    print()
    print("  Retry round totals")
    print()
    print(f"  Retried     : {stats['total']}")
    print(f"  Downloaded  : {stats['downloaded']}")
    if stats["skipped"]:
        print(f"  Skipped     : {stats['skipped']}")
    print(f"  Failed      : {stats['failed']}")
    print(f"  Unresolved  : {stats['unresolved']}")


def _run_with_retries(
    items: list,
    chosen_height: int | None,
    playlist_dir: Path | None,
    known_count: int | None = None,
    verbose: bool = False,
    preserve_chapters: bool = False,
    quality_fallback: bool = False,
) -> None:
    """Run the initial playlist pass, then offer explicit retries for pending items.

    The initial pass processes every item and reports the playlist summary. If
    any item failed or could not be resolved, the user is asked whether to retry
    only those items; the retry reuses the same chosen quality and playlist
    directory and re-runs the shared :func:`_process_items` on the pending
    subset in original playlist order. Retries are always explicit — a retry is
    offered again only while pending items remain and the user answers yes.

    ``known_count`` is the initial run's progress denominator (it may be None
    for a playlist of unknown size, so no fabricated total is shown).
    ``quality_fallback`` propagates the non-interactive ``--quality`` fallback
    semantics to the per-item selection.
    """
    stats, failed, skipped, unresolved = _process_items(
        items, chosen_height, playlist_dir, "Playlist item", known_count,
        verbose=verbose, preserve_chapters=preserve_chapters,
        quality_fallback=quality_fallback,
    )
    _report_playlist_complete(stats, failed, skipped, unresolved)
    round_no = 1
    while failed or unresolved:
        pending = sorted(failed + unresolved, key=lambda d: d["index"])
        answer = _read_line("\nRetry failed/unresolved items? [y/N]: ")
        if answer is None or answer.strip().lower() not in ("y", "yes"):
            break
        retry_items = [
            {"index": p["index"], "entry": p["entry"], "resolved": p["resolved"], "qualities": p["qualities"]}
            for p in pending
        ]
        print()
        print("Retrying failed/unresolved items...")
        _debug(f"Retry round: {round_no} ({len(retry_items)} item(s))", verbose)
        stats, failed, skipped, unresolved = _process_items(
            retry_items, chosen_height, playlist_dir, "Retry item", len(retry_items),
            verbose=verbose, preserve_chapters=preserve_chapters,
            quality_fallback=quality_fallback,
        )
        _print_retry_summary(stats)
        round_no += 1


def _process_playlist(
    info: dict,
    chosen_height: int | None = None,
    playlist_dir: Path | None = None,
    per_item: list | None = None,
    verbose: bool = False,
    preserve_chapters: bool = False,
    quality_fallback: bool = False,
) -> dict:
    """Download every playlist entry sequentially via the single-video pipeline.

    Entries are processed one at a time; malformed or unresolvable entries are
    reported and skipped without stopping the remaining playlist. A summary is
    printed once all entries have been iterated. Returns the tally dict.

    When ``chosen_height``, ``playlist_dir`` and ``per_item`` are provided
    (from the planning phase in :func:`_run_download`), every item is
    downloaded into the dedicated playlist directory using that single
    preselected quality instead of prompting per item. ``per_item`` holds the
    already-resolved entry info and its precomputed quality map, so neither
    entries nor ``select_formats`` are recomputed here. ``quality_fallback``
    propagates the non-interactive ``--quality`` fallback semantics.
    """
    items = _build_playlist_items(info, per_item)
    stats, failed, skipped, unresolved = _process_items(
        items, chosen_height, playlist_dir, "Playlist item", _safe_playlist_count(info),
        verbose=verbose, preserve_chapters=preserve_chapters,
        quality_fallback=quality_fallback,
    )
    _report_playlist_complete(stats, failed, skipped, unresolved)
    return stats


def _debug(message: str, verbose: bool) -> None:
    """Print a single ``[DEBUG]`` diagnostic line when verbose mode is enabled."""
    if verbose:
        print(f"[DEBUG] {message}")


def _run_download(
    base: Path | None = None,
    url: str | None = None,
    verbose: bool = False,
    preserve_chapters: bool = False,
    quality: int | None = None,
) -> None:
    print("DownV - Media Downloader")
    _debug("Verbose mode enabled", verbose)
    if quality is not None:
        _debug(f"Quality requested: {quality}p", verbose)
    print()
    if url is None:
        url = prompt_for_url()
        if url is None:
            _menu_cancelled()
            return
        _debug("URL source: interactive", verbose)
    else:
        _debug("URL source: command line", verbose)
    print()
    print("Fetching media information...")

    try:
        info = get_media_info(url)
    except MediaInfoError as exc:
        print("✗ Failed to retrieve media information.")
        print()
        print(f"Reason: {exc}")
        return

    if "entries" in info:
        _debug("Media type: playlist", verbose)
        if _handle_playlist(info):
            print()
            print("✓ Playlist confirmed")
            print()
            title = info.get("title") or info.get("playlist_title") or info.get("id") or "Playlist"
            _debug(f"Playlist title: {title}", verbose)
            try:
                chosen_height, per_item = _plan_playlist(info, quality)
            except _MenuCancelled:
                print()
                print("Download cancelled.")
                return
            _debug(f"Selected quality: {chosen_height}p", verbose)
            chapters = [r["resolved"] for r in per_item if r.get("resolved")]
            if any(r.get("chapters") for r in chapters):
                preserve_chapters = _ask_preserve_chapters(
                    "\nDownload chapters for videos that have them? [y/N]: "
                )
            try:
                playlist_dir = _playlist_output_dir(title, base)
            except OutputDirectoryError as exc:
                print("✗ Could not create playlist directory.")
                print()
                print(f"Reason: {exc}")
                return
            _debug(f"Output directory: {playlist_dir}", verbose)
            print(f"✓ Playlist directory ready: {playlist_dir}/")
            print()
            _run_with_retries(
                _build_playlist_items(info, per_item),
                chosen_height=chosen_height,
                playlist_dir=playlist_dir,
                known_count=_safe_playlist_count(info),
                verbose=verbose,
                preserve_chapters=preserve_chapters,
                quality_fallback=quality is not None,
            )
        else:
            print()
            print("Download cancelled.")
        return

    _debug("Media type: single video", verbose)
    try:
        out_dir = base if base is not None else resolve_output_directory()
    except OutputDirectoryError as exc:
        print("✗ Could not create output directory.")
        print()
        print(f"Reason: {exc}")
        return
    _debug(f"Output directory: {out_dir}", verbose)
    was_missing = not out_dir.exists()
    if was_missing:
        print(f"✓ Created output directory: {out_dir}/")
    else:
        print(f"✓ Output directory ready: {out_dir}/")
    print()
    if info.get("chapters"):
        preserve_chapters = _ask_preserve_chapters("\nDownload chapters? [y/N]: ")
    _download_video(info, output_dir=out_dir, verbose=verbose, preserve_chapters=preserve_chapters, quality=quality)


HELP_TEXT = """DownV - Media Downloader

Usage:
  downv [OPTIONS] [URL]
  downv history <COMMAND>

Options:
  -h, --help              Show this help message and exit
  -V, --version           Show version information and exit
  -v, --verbose           Enable verbose diagnostics
      --output DIR        Save downloads into DIR
      --output=DIR        Save downloads into DIR (equivalent to --output DIR)
      --quality HEIGHT    Download at the specified quality (e.g. 1080, 720)

With no URL, DownV prompts for one. A URL may also be passed directly as a
positional argument. The --output and --quality flags may appear before or
after the URL.

When --quality is provided, the interactive quality-selection menu is skipped
and the requested quality is used automatically. Without --quality the
interactive menu is shown as before. For playlists, --quality applies the
same quality to every video.

History subcommands:
  history count       Show the number of recorded downloads
  history search      Search recorded downloads
  history remove      Remove a recorded video by ID
  history clear       Clear the download history
  history detail      Show details for a recorded video
  history             Show the download history

Exit codes:
  0  Success / normal cancellation
  1  Error / invalid usage
  130 Interrupted with Ctrl+C

Requirements:
  Python >= 3.10
  yt-dlp
  FFmpeg is required when the selected format needs merging
"""


def _print_help() -> None:
    print(HELP_TEXT, end="")


def _print_version() -> None:
    print(f"DownV {__version__}")


def _main() -> int | None:
    args = sys.argv[1:]

    for arg in args:
        if arg in ("-h", "--help"):
            _print_help()
            return 0
        if arg in ("-V", "--version"):
            _print_version()
            return 0

    base = None
    verbose = False
    quality = None
    remaining = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--output":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                print("Error: --output requires a directory path")
                return 1
            base = resolve_output_directory(args[i + 1])
            i += 2
        elif arg.startswith("--output="):
            value = arg.split("=", 1)[1]
            if not value:
                print("Error: --output requires a directory path")
                return 1
            base = resolve_output_directory(value)
            i += 1
        elif arg == "--quality":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                print("Error: --quality requires a height value (e.g. --quality 1080)")
                return 1
            try:
                quality = int(args[i + 1])
            except ValueError:
                print(f"Error: --quality must be a positive integer, got: {args[i + 1]!r}")
                return 1
            if quality <= 0:
                print(f"Error: --quality must be a positive integer, got: {quality}")
                return 1
            i += 2
        elif arg.startswith("--quality="):
            value = arg.split("=", 1)[1]
            if not value:
                print("Error: --quality requires a height value (e.g. --quality=1080)")
                return 1
            try:
                quality = int(value)
            except ValueError:
                print(f"Error: --quality must be a positive integer, got: {value!r}")
                return 1
            if quality <= 0:
                print(f"Error: --quality must be a positive integer, got: {quality}")
                return 1
            i += 1
        elif arg in ("-v", "--verbose"):
            verbose = True
            i += 1
        elif arg.startswith("-") and arg != "-":
            print(f"Error: unknown option: {arg}")
            print()
            print("Usage:")
            print("  downv [-v] [--output <dir>] [--quality <height>] [URL]   Download a single video (URL optional)")
            return 1
        else:
            remaining.append(arg)
            i += 1

    if remaining and remaining[0] == "history":
        if len(remaining) >= 2 and remaining[1] == "remove":
            if len(remaining) >= 3:
                remove_history(remaining[2])
            else:
                print("Usage: downv history remove <video_id>")
            return
        if len(remaining) >= 2 and remaining[1] == "clear":
            if len(remaining) >= 3:
                print("Usage: downv history clear")
            else:
                clear_history()
            return
        if len(remaining) >= 2 and remaining[1] == "search":
            if len(remaining) >= 3:
                search_history(remaining[2])
            else:
                print("Usage: downv history search <query>")
            return
        if len(remaining) >= 2 and remaining[1] == "count":
            if len(remaining) >= 3:
                print("Usage: downv history count")
            else:
                show_history_count()
            return
        if len(remaining) >= 2:
            show_history_detail(remaining[1])
        else:
            show_history()
        return

    if len(remaining) > 1:
        print(f"Error: unexpected extra arguments: {' '.join(remaining[1:])}")
        print()
        print("Usage:")
        print("  downv [-v] [--output <dir>] [--quality <height>] [URL]   Download a single video (URL optional)")
        return 1

    url = remaining[0] if remaining else None
    _run_download(base, url, verbose, quality=quality)


def main() -> int:
    """Top-level CLI entry point with a graceful error boundary.

    Returns an exit code: ``0`` on success, ``130`` when the user cancels with
    ``Ctrl+C``, and ``1`` on an unexpected error. This is the single place that
    turns ``KeyboardInterrupt`` and unexpected exceptions into concise, user
    facing messages instead of raw Python tracebacks.
    """
    try:
        result = _main()
        return result if isinstance(result, int) else 0
    except KeyboardInterrupt:
        print()
        print("Download cancelled.")
        return 130
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
