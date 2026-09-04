"""Video download engine using yt-dlp."""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadError, sanitize_filename

from downv import history
from downv.formats import SelectedMediaFormat


class DownloadFailure(Exception):
    """Raised when a download cannot be completed."""


_VERBOSE = False


def set_verbose(value: bool) -> None:
    """Enable or disable verbose yt-dlp output for subsequent downloads.

    Kept as simple module state so the public ``download_media``/
    ``download_audio`` signatures stay stable (they are mocked throughout the
    test suite). The CLI calls this once at startup based on ``--verbose`` so
    normal runs stay clean and verbose runs surface yt-dlp diagnostics.
    """
    global _VERBOSE
    _VERBOSE = bool(value)


def ffmpeg_available() -> bool:
    """Return True if FFmpeg is available on the system PATH."""
    return shutil.which("ffmpeg") is not None


class _QuietLogger:
    """yt-dlp logger that forwards messages to Python's logging.

    Verbose downloads (see :func:`set_verbose`) use this logger so yt-dlp's own
    ``[download]``/``[Merger]`` diagnostics surface through Python's
    ``logging`` (under ``downv.downloader``), where the CLI enables a handler in
    verbose mode. Normal downloads use :class:`_MutedLogger` instead.
    """

    def debug(self, msg):
        logging.getLogger("downv.downloader").debug(msg)

    def warning(self, msg):
        logging.getLogger("downv.downloader").warning(msg)

    def error(self, msg):
        logging.getLogger("downv.downloader").error(msg)


class _MutedLogger:
    """yt-dlp logger that discards every message.

    Used in normal (non-verbose) downloads so background framework noise (for
    example from FFmpeg postprocessors) is never echoed to stdout; DownV prints
    its own concise progress messages instead.
    """

    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _media_for_base(output_dir: Path, base: str) -> Path:
    """Return the media file in ``output_dir`` whose base name is ``base``.

    The base name is the title-derived stem; the extension is whatever yt-dlp
    produced (mp4, mkv, webm, ...). Sidecar and fragment files are ignored.
    """
    if not output_dir.is_dir():
        return None
    for child in output_dir.iterdir():
        if child.is_file() and child.name.startswith(base + "."):
            if child.name.endswith(".info.json") or child.name.endswith(".part"):
                continue
            return child
    return None


def _valid_media_state(record: dict, path: Path) -> bool:
    """Return True if ``path`` still represents the recorded download.

    Uses filesystem metadata only (no hashing): the file's size must match the
    recorded ``file_size`` and its mtime must not be newer than the recorded
    ``downloaded_at`` timestamp. Records without the ``file_size`` field
    (pre-dating Step 5B.3) are not trusted and are treated as invalid so they
    can never cause a false "already downloaded" result.
    """
    file_size = record.get("file_size")
    downloaded_at = record.get("downloaded_at")
    if file_size is None or not downloaded_at:
        return False
    try:
        recorded_epoch = datetime.fromisoformat(downloaded_at)
        size = path.stat().st_size
        mtime = path.stat().st_mtime
    except (OSError, ValueError, TypeError):
        return False
    if recorded_epoch.tzinfo is None:
        recorded_epoch = recorded_epoch.replace(tzinfo=timezone.utc)
    return size == file_size and mtime <= recorded_epoch.timestamp()


def find_existing_download(info: dict, media_type: str = "video") -> Path:
    """Return the path of a valid, previously completed download of this video.

    Duplicate detection is based on the YouTube ``video_id`` combined with the
    ``media_type`` (``"video"`` or ``"audio"``), so a video download and the
    audio-only version of the same media are never mistaken for each other. A
    record only counts as a duplicate when the file at its recorded path still
    exists AND still represents that recorded download (see
    ``_valid_media_state``). Any record whose file was deleted, replaced,
    modified after the download, or replaced by a manually created placeholder
    is NOT treated as a duplicate; the caller proceeds to download again.

    Returns None when no valid prior download of this media type exists.
    """
    video_id = info.get("id")
    if not video_id:
        return None
    try:
        records = history.find_downloads(video_id)
    except history.HistoryError as exc:
        print(f"Warning: {exc}")
        return None
    for record in reversed(records):
        if record.get("media_type", "video") != media_type:
            continue
        filepath = record.get("filepath")
        if not filepath:
            continue
        path = Path(filepath)
        if path.is_file() and _valid_media_state(record, path):
            return path
    return None


def _safe_base(title: str) -> str:
    return sanitize_filename(title, restricted=False) or "video"


def _resolve_unique_base(title: str, output_dir: Path) -> str:
    """Return a safe, collision-free base file name for the given title.

    ``Title`` is preferred; if a media file already exists under that name
    (belonging to a different video), ``Title (1)``, ``Title (2)``, ... are
    tried instead. Unrelated existing files are never overwritten.
    """
    base = _safe_base(title)
    candidate = base
    counter = 1
    while _media_for_base(output_dir, candidate) is not None:
        candidate = f"{base} ({counter})"
        counter += 1
    return candidate


def _make_options(
    url: str,
    selected: SelectedMediaFormat,
    output_dir: Path,
    base: str,
    preserve_chapters: bool = False,
) -> dict:
    options = {
        "format": selected.format_selector,
        "outtmpl": str(output_dir / f"{base}.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "overwrites": False,
        "logger": _QuietLogger() if _VERBOSE else _MutedLogger(),
        "noprogress": not _VERBOSE,
        "quiet": not _VERBOSE,
    }
    if preserve_chapters:
        options["postprocessors"] = [
            {
                "key": "FFmpegMetadata",
                "add_metadata": False,
                "add_chapters": True,
                "add_infojson": False,
            }
        ]
    return options


def _record(
    info: dict,
    result: Path,
    quality: int | None,
    media_type: str = "video",
) -> None:
    duration = info.get("duration")
    try:
        history.record_download(
            video_id=info.get("id") or "",
            title=info.get("title") or result.stem,
            url=info.get("webpage_url") or info.get("original_url") or "",
            filename=result.name,
            filepath=str(result),
            quality=quality,
            duration=duration,
            file_size=result.stat().st_size,
            media_type=media_type,
        )
    except history.HistoryError as exc:
        print(f"Warning: {exc}")


def _download(
    url: str,
    selected: SelectedMediaFormat,
    output_dir: Path,
    base: str,
    preserve_chapters: bool = False,
) -> None:
    options = _make_options(url, selected, output_dir, base, preserve_chapters)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except DownloadError as exc:
        reason = exc.exc_info[1] if exc.exc_info and exc.exc_info[1] else str(exc)
        raise DownloadFailure(str(reason)) from exc


def _make_audio_options(
    url: str,
    selected_audio,
    output_dir: Path,
    base: str,
) -> dict:
    options = {
        "format": selected_audio.format_selector,
        "outtmpl": str(output_dir / f"{base}.%(ext)s"),
        "noplaylist": True,
        "overwrites": False,
        "logger": _QuietLogger() if _VERBOSE else _MutedLogger(),
        "noprogress": not _VERBOSE,
        "quiet": not _VERBOSE,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }
    return options


def download_audio(
    info: dict,
    selected_audio,
    output_dir: Path,
) -> Path:
    """Download the audio of ``info`` as an MP3 using the selected audio stream.

    Returns the path to the completed file. FFmpeg is required to extract and
    transcode the audio to MP3; a ``DownloadFailure`` is raised when it is not
    available. Duplicate detection is scoped to audio-only downloads, so a
    previously downloaded video of the same media never blocks the audio
    version and vice versa.
    """
    url = info.get("webpage_url") or info.get("original_url")
    if not url:
        raise DownloadFailure("No downloadable URL available.")
    if not ffmpeg_available():
        raise DownloadFailure("FFmpeg is required for audio extraction.")

    existing = find_existing_download(info, media_type="audio")
    if existing:
        return existing

    base = _resolve_unique_base(info.get("title") or "video", output_dir)
    options = _make_audio_options(url, selected_audio, output_dir, base)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except DownloadError as exc:
        reason = exc.exc_info[1] if exc.exc_info and exc.exc_info[1] else str(exc)
        raise DownloadFailure(str(reason)) from exc

    result = _media_for_base(output_dir, base)
    if result is None:
        raise DownloadFailure("Download reported success but no file was found.")

    _record(info, result, quality=None, media_type="audio")
    return result


def download_media(
    info: dict,
    selected: SelectedMediaFormat,
    output_dir: Path,
    preserve_chapters: bool = False,
) -> Path:
    """Download the media described by ``info`` using the selected formats.

    Returns the path to the completed file. If the same video is already
    downloaded (matching history record whose file still exists), the existing
    file is returned and nothing is re-downloaded. A successful download is
    recorded in the download history only after the final file exists.

    When ``preserve_chapters`` is True, yt-dlp's native ``FFmpegMetadata``
    postprocessor embeds any chapters into the final file after merging.
    """
    url = info.get("webpage_url") or info.get("original_url")
    if not url:
        raise DownloadFailure("No downloadable URL available.")

    existing = find_existing_download(info)
    if existing:
        return existing

    base = _resolve_unique_base(info.get("title") or "video", output_dir)
    _download(url, selected, output_dir, base, preserve_chapters)

    result = _media_for_base(output_dir, base)
    if result is None:
        raise DownloadFailure("Download reported success but no file was found.")

    _record(info, result, quality=selected.height, media_type="video")
    return result