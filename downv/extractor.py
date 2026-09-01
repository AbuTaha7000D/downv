"""Media information extraction using yt-dlp."""

import yt_dlp


class MediaInfoError(Exception):
    """Raised when media information cannot be retrieved."""


def get_media_info(url: str) -> dict:
    """Fetch metadata about the media at ``url`` without downloading it."""
    options = {
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        reason = exc.exc_info[1] if exc.exc_info and exc.exc_info[1] else str(exc)
        raise MediaInfoError(str(reason)) from exc
    except yt_dlp.utils.ExtractorError as exc:
        raise MediaInfoError(str(exc)) from exc
