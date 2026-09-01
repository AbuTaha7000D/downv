"""Persistent download metadata stored in a JSON history file.

The downloader and future History UI interact with this module only through
the public functions; no caller reads or writes the JSON file directly.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from downv.paths import get_data_directory

HISTORY_FILENAME = "history.json"

_SCHEMA_VERSION = 1


class HistoryError(Exception):
    """Raised when the download history cannot be read or written safely."""


def _history_path() -> Path:
    return get_data_directory() / HISTORY_FILENAME


def _validate(history: dict) -> bool:
    """Return True if ``history`` has a usable structure."""
    return isinstance(history, dict) and isinstance(history.get("downloads"), list)


def _load() -> list:
    """Read the history file and return the current download records.

    Returns an empty list when no history file exists yet. Raises
    ``HistoryError`` (without modifying the file) when the file is corrupted.
    """
    path = _history_path()
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            history = json.load(fh)
    except (OSError, ValueError) as exc:
        raise HistoryError(
            f"Download history file is corrupted and was left untouched: {path}"
        ) from exc
    if not _validate(history):
        raise HistoryError(
            f"Download history file has an unexpected structure and was left untouched: {path}"
        )
    return list(history["downloads"])


def _save(downloads: list) -> None:
    """Atomically write the given records to the history file.

    The file is first written to a temporary file in the same directory and
    then moved into place so a crash never leaves a half-written history.json.
    """
    path = _history_path()
    history = {"version": _SCHEMA_VERSION, "downloads": downloads}
    payload = json.dumps(history, indent=2, ensure_ascii=False) + "\n"
    tmp_path = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".history.", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise HistoryError(f"Could not write download history: {exc}") from exc


def get_download_history() -> list:
    """Return all recorded downloads, newest first.

    Records are sorted by their ``downloaded_at`` timestamp in descending
    order (ties fall back to the existing file order). Returns a list of dicts.
    """
    downloads = _load()

    def _sort_key(record: dict):
        try:
            return datetime.fromisoformat(record.get("downloaded_at") or "")
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    return sorted(downloads, key=_sort_key, reverse=True)


def find_downloads(video_id: str) -> list:
    """Return all download records for ``video_id``, oldest first."""
    if not video_id:
        return []
    return [r for r in _load() if r.get("video_id") == video_id]


def find_download(video_id: str) -> dict | None:
    """Return the most recent download record for ``video_id``, or None."""
    records = find_downloads(video_id)
    return _most_recent(records)


def _most_recent(records: list) -> dict | None:
    """Return the record with the latest ``downloaded_at`` in ``records``."""
    if not records:
        return None

    def _key(record: dict):
        try:
            return datetime.fromisoformat(record.get("downloaded_at") or "")
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    return max(records, key=_key)


def count_history() -> int:
    """Return the number of recorded downloads."""
    return len(_load())


def remove_download(video_id: str) -> dict | None:
    """Remove the history metadata for ``video_id`` and return the removed record.

    Only the metadata is removed; the corresponding media file is never
    deleted. If the video has no record, no change is made and None is returned.
    """
    if not video_id:
        return None
    downloads = _load()
    for index, existing in enumerate(downloads):
        if existing.get("video_id") == video_id:
            removed = downloads.pop(index)
            _save(downloads)
            return removed
    return None


def clear_history() -> None:
    """Remove all download metadata.

    Metadata only: media files on disk are never deleted. The updated (empty)
    history is persisted atomically.
    """
    _save([])


def record_download(
    *,
    video_id: str,
    title: str,
    url: str,
    filename: str,
    filepath: str,
    quality: int,
    duration: float | None,
    file_size: int,
) -> dict:
    """Record a completed download and return the stored record.

    The ``file_size`` (bytes) of the completed media is stored alongside the
    ``downloaded_at`` timestamp so future duplicate checks can tell whether
    the file on disk still represents this recorded download.

    If a record for the same video already exists it is updated in place
    (one current record per video); otherwise a new record is appended.
    Raises ``HistoryError`` if the current history cannot be preserved (for
    example when an existing file is corrupted) so no metadata is lost.
    """
    record = {
        "video_id": video_id,
        "title": title,
        "url": url,
        "filename": filename,
        "filepath": filepath,
        "quality": int(quality),
        "duration": int(duration) if duration else None,
        "file_size": int(file_size),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    downloads = _load()
    for index, existing in enumerate(downloads):
        if existing.get("video_id") == video_id:
            downloads[index] = record
            break
    else:
        downloads.append(record)
    _save(downloads)
    return record