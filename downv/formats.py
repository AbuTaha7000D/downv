"""Processing of yt-dlp format data for quality selection."""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class SelectedMediaFormat:
    """A concrete download choice for a given quality (height).

    ``video_fmt_id`` and ``audio_fmt_id`` are the exact yt-dlp format IDs that
    will be downloaded. The estimated ``size_bytes`` corresponds to the total
    of those streams.
    """

    height: int
    video_fmt_id: str
    audio_fmt_id: Optional[str]
    size_bytes: Optional[float]

    @property
    def label(self) -> str:
        return f"{self.height}p"

    @property
    def needs_merge(self) -> bool:
        return self.audio_fmt_id is not None

    @property
    def format_selector(self) -> str:
        if self.audio_fmt_id:
            return f"{self.video_fmt_id}+{self.audio_fmt_id}"
        return self.video_fmt_id


def format_size(size_bytes: Optional[float]) -> str:
    """Return a human-readable size string, or 'Size unknown' if unknown."""
    if not size_bytes:
        return "Size unknown"
    size = float(size_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    value = size
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"~{int(value)} {unit}"
            return f"~{value:.1f} {unit}"
        value /= 1024
    return "Size unknown"


def _format_size_of(fmt: dict) -> Optional[float]:
    return fmt.get("filesize") or fmt.get("filesize_approx")


def _video_quality_key(fmt: dict) -> tuple:
    size = _format_size_of(fmt)
    return (size is not None, fmt.get("tbr") or 0, size or 0)


def _audio_quality_key(fmt: dict) -> tuple:
    size = _format_size_of(fmt)
    return (size is not None, fmt.get("abr") or fmt.get("tbr") or 0, size or 0)


def is_video_only(fmt: dict) -> bool:
    """True for formats carrying video but no audio stream."""
    return (
        bool(fmt.get("vcodec"))
        and fmt.get("vcodec") != "none"
        and (not fmt.get("acodec") or fmt.get("acodec") == "none")
    )


def is_audio_only(fmt: dict) -> bool:
    """True for formats carrying audio but no video stream."""
    return (
        bool(fmt.get("acodec"))
        and fmt.get("acodec") != "none"
        and (not fmt.get("vcodec") or fmt.get("vcodec") == "none")
    )


def is_combined(fmt: dict) -> bool:
    """True for formats carrying both video and audio in one stream."""
    return (
        bool(fmt.get("vcodec"))
        and fmt.get("vcodec") != "none"
        and bool(fmt.get("acodec"))
        and fmt.get("acodec") != "none"
    )


def select_formats(info: dict) -> Dict[int, SelectedMediaFormat]:
    """Unify quality selection into concrete download choices.

    For every available video height, pick the best video-only stream at that
    exact height plus the best audio stream (merged), or a single combined
    stream when no separate audio exists. The exact format IDs are recorded so
    the downloader reuses the same streams used for the size estimate.
    """
    formats: List[dict] = info.get("formats") or []

    video_only = [f for f in formats if is_video_only(f) and f.get("height")]
    combined = [f for f in formats if is_combined(f) and f.get("height")]
    audio_only = [f for f in formats if is_audio_only(f)]

    best_audio = max(audio_only, key=_audio_quality_key, default=None)

    heights = sorted({f["height"] for f in video_only + combined}, reverse=True)
    result: Dict[int, SelectedMediaFormat] = {}

    for height in heights:
        vids = [f for f in video_only if f.get("height") == height]
        combs = [f for f in combined if f.get("height") == height]

        if vids:
            video = max(vids, key=_video_quality_key)
            video_size = _format_size_of(video)
            if best_audio:
                audio_size = _format_size_of(best_audio)
                total = (
                    video_size + audio_size
                    if video_size is not None and audio_size is not None
                    else None
                )
                result[height] = SelectedMediaFormat(
                    height, video["format_id"], best_audio["format_id"], total
                )
            else:
                result[height] = SelectedMediaFormat(
                    height, video["format_id"], None, video_size
                )
        elif combs:
            combo = max(combs, key=_video_quality_key)
            result[height] = SelectedMediaFormat(
                height, combo["format_id"], None, _format_size_of(combo)
            )

    return result


def pick_quality_by_height(
    qualities: Dict[int, SelectedMediaFormat], height: int
) -> SelectedMediaFormat | None:
    """Select the best format for the requested ``height``.

    Returns an exact match when available. Otherwise falls back to the closest
    height at or below the requested value (e.g. 1080 requested with only 720
    available returns 720). Returns ``None`` only when ``qualities`` is empty.
    """
    if not qualities:
        return None
    if height in qualities:
        return qualities[height]
    lower = [h for h in qualities if h <= height]
    if lower:
        return qualities[max(lower)]
    return qualities[min(qualities)]
