"""Media information extraction using yt-dlp."""

import yt_dlp

from downv import downloader
from downv.downloader import _MutedLogger, _QuietLogger


class MediaInfoError(Exception):
    """Raised when media information cannot be retrieved."""


def get_media_info(url: str) -> dict:
    """Fetch metadata about the media at ``url`` without downloading it.

    Extraction is strictly metadata-only: ``skip_download`` with
    ``extract_info(..., download=False)`` guarantees no media bytes are written.
    ``extract_flat='in_playlist'`` returns playlist entries as lightweight
    stubs (id/title/url) rather than fully resolving every item up-front, so a
    playlist can be detected, described and counted without processing items
    through the download path. ``quiet`` suppresses yt-dlp's ``[download]
    Downloading item X of Y`` progress chatter during this detection pass.
    Plain (non-playlist) URLs are unaffected because they are not inside a
    playlist ``extra_info`` and therefore are still fully resolved.

    The configured ``logger`` mirrors the download path (see :func:`set_verbose`):
    normal runs use a muted logger so yt-dlp ``WARNING``/``DEBUG`` lines never
    leak into regular CLI output, while verbose runs forward them onto the
    ``downv.downloader`` logger where the CLI surfaces them in ``--verbose``.
    """
    options = {
        "skip_download": True,
        "extract_flat": "in_playlist",
        "quiet": True,
        "logger": _QuietLogger() if downloader._VERBOSE else _MutedLogger(),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        reason = exc.exc_info[1] if exc.exc_info and exc.exc_info[1] else str(exc)
        raise MediaInfoError(str(reason)) from exc
    except yt_dlp.utils.ExtractorError as exc:
        raise MediaInfoError(str(exc)) from exc
