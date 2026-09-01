"""CLI entry point for DownV."""

import re
import sys
import termios
import tty
from pathlib import Path
from typing import Dict

from yt_dlp.utils import sanitize_filename

from downv import history
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
    select_formats,
)
from downv.paths import OutputDirectoryError, get_output_directory


def prompt_for_url() -> str:
    while True:
        url = input("Enter URL: ").strip()
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
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
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


def select_quality(qualities: Dict[int, SelectedMediaFormat]) -> SelectedMediaFormat:
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
        render()
        key = read_key()
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

    answer = input(prompt).strip().lower()
    return answer in ("y", "yes")


def _commit_download(info: dict, selected: SelectedMediaFormat, output_dir: Path) -> str:
    """Run duplicate detection + FFmpeg check + media download for a resolved
    item using a concrete format and output directory.

    Shared by standalone and playlist item downloads. Returns the outcome:
    ``"skipped"`` (already downloaded), ``"failed"``, or ``"downloaded"``.
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
        return "failed"

    print()
    print("Starting download...")
    print()
    try:
        download_media(info, selected, output_dir)
    except DownloadFailure as exc:
        print("✗ Download failed.")
        print()
        print(f"Reason: {exc}")
        return "failed"

    print()
    print("✓ Download completed")
    return "downloaded"


def _download_video(
    info: dict,
    selected: SelectedMediaFormat | None = None,
    output_dir: Path | None = None,
) -> str:
    """Process a single video through the shared download pipeline.

    If ``selected`` is provided (playlist items using a preselected playlist
    quality), the per-video quality picker is skipped. If ``output_dir`` is
    provided (playlist directory), it is used instead of the default output
    directory. Standalone calls keep their existing interactive behaviour.

    Returns the outcome so playlist orchestration can tally results:
    ``"downloaded"`` when a new download completed, ``"skipped"`` when
    duplicate detection found a valid prior download, or ``"failed"`` when
    the item could not be downloaded for any reason.
    """
    display_info(info)

    if selected is None:
        qualities = select_formats(info)
        if not qualities:
            print("No video formats available.")
            return "failed"
        print()
        selected = select_quality(qualities)

    if output_dir is None:
        output_dir = Path.home() / "Videos" / "downv"
        was_missing = not output_dir.exists()
        try:
            output_dir = get_output_directory()
        except OutputDirectoryError as exc:
            print("✗ Could not create output directory.")
            print()
            print(f"Reason: {exc}")
            return "failed"
        if was_missing:
            print(f"✓ Created output directory: {output_dir}/")
        else:
            print(f"✓ Output directory ready: {output_dir}/")

    return _commit_download(info, selected, output_dir)


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


_MAX_PLAYLIST_DIR_NAME = 120


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


def _playlist_output_dir(title: str) -> Path:
    """Return the dedicated subdirectory for a playlist, creating it if needed.

    Lives under the default output directory so standalone videos are
    unaffected: ``~/Videos/downv/<Safe Playlist Title>/``.
    """
    base = get_output_directory()
    target = base / playlist_dir_name(title)
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
    return selected.height


def _plan_playlist(info: dict) -> tuple:
    """Resolve every entry once and compute its per-item quality map.

    Each entry is resolved exactly once and its ``select_formats`` map is
    computed exactly once. Returns ``(chosen_height_or_None, per_item)`` where
    ``per_item`` is a list parallel to ``info["entries"]``; each element is a
    dict with ``"resolved"`` (an info dict or None) and ``"qualities"`` (the
    ``{height: SelectedMediaFormat}`` map, empty for unresolvable items). The
    same ``per_item`` list is reused for both menu aggregation and download, so
    entries and formats are never materialised twice.
    """
    per_item = []
    for entry in info.get("entries") or []:
        resolved = _resolve_playlist_entry(entry)
        qualities = select_formats(resolved) if resolved else {}
        per_item.append({"resolved": resolved, "qualities": qualities})
    chosen_height = _choose_playlist_quality(per_item)
    return chosen_height, per_item


def _process_playlist(
    info: dict,
    chosen_height: int | None = None,
    playlist_dir: Path | None = None,
    per_item: list | None = None,
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
    entries nor ``select_formats`` are recomputed here.
    """
    stats = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "unresolved": 0}
    entries = info.get("entries") or []
    total = _safe_playlist_count(info)
    for number, entry in enumerate(entries, start=1):
        stats["total"] += 1
        denominator = f"/{total}" if total else ""
        print(f"Playlist item {number}{denominator}")
        entry_title = entry.get("title", "Unknown") if isinstance(entry, dict) else "Unknown"
        print(f"  Title : {entry_title}")
        if per_item is not None:
            resolved = per_item[number - 1]["resolved"]
            item_qualities = per_item[number - 1]["qualities"]
        else:
            resolved = _resolve_playlist_entry(entry)
            item_qualities = None
        if resolved is None:
            stats["unresolved"] += 1
            print()
            print(f"✗ Could not resolve playlist item {number}.")
            print()
            continue
        print()
        try:
            if chosen_height is not None:
                if item_qualities is None:
                    item_qualities = select_formats(resolved)
                selected = item_qualities.get(chosen_height)
                if selected is None:
                    stats["failed"] += 1
                    print(f"  Quality {chosen_height}p not available for this item.")
                    print()
                    continue
                outcome = _download_video(resolved, selected=selected, output_dir=playlist_dir)
            else:
                outcome = _download_video(resolved)
            stats[outcome] += 1
        except Exception as exc:
            stats["failed"] += 1
            print(f"✗ Failed to download playlist item {number}.")
            print()
            print(f"Reason: {exc}")

    _print_playlist_summary(stats)
    return stats


def _run_download() -> None:
    print("DownV - Media Downloader")
    print()
    url = prompt_for_url()
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
        if _handle_playlist(info):
            print()
            print("✓ Playlist confirmed")
            print()
            title = info.get("title") or info.get("playlist_title") or info.get("id") or "Playlist"
            chosen_height, per_item = _plan_playlist(info)
            try:
                playlist_dir = _playlist_output_dir(title)
            except OutputDirectoryError as exc:
                print("✗ Could not create playlist directory.")
                print()
                print(f"Reason: {exc}")
                return
            print(f"✓ Playlist directory ready: {playlist_dir}/")
            print()
            _process_playlist(info, chosen_height=chosen_height, playlist_dir=playlist_dir, per_item=per_item)
        else:
            print()
            print("Download cancelled.")
        return

    _download_video(info)


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "history":
        if len(args) >= 2 and args[1] == "remove":
            if len(args) >= 3:
                remove_history(args[2])
            else:
                print("Usage: downv history remove <video_id>")
            return
        if len(args) >= 2 and args[1] == "clear":
            if len(args) >= 3:
                print("Usage: downv history clear")
            else:
                clear_history()
            return
        if len(args) >= 2 and args[1] == "search":
            if len(args) >= 3:
                search_history(args[2])
            else:
                print("Usage: downv history search <query>")
            return
        if len(args) >= 2 and args[1] == "count":
            if len(args) >= 3:
                print("Usage: downv history count")
            else:
                show_history_count()
            return
        if len(args) >= 2:
            show_history_detail(args[1])
        else:
            show_history()
        return
    if args:
        print(f"Unknown command: {args[0]}")
        print()
        print("Usage:")
        print("  downv                       Download a video interactively")
        print("  downv history               Show download history")
        print("  downv history <id>          Show details for one video")
        print("  downv history remove <id>   Remove a history record (metadata only)")
        print("  downv history clear         Clear all history records (metadata only)")
        print("  downv history search <q>    Search history by title or video ID")
        print("  downv history count         Show number of recorded downloads")
        return
    _run_download()


if __name__ == "__main__":
    main()
