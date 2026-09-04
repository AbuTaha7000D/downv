"""Unit tests for the DownV history API."""

from pathlib import Path

import pytest

from downv import history, paths


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Point the history storage at a fresh temporary directory."""
    monkeypatch.setattr(history, "get_data_directory", lambda: tmp_path)
    return tmp_path


def _make_record(video_id, title, timestamp):
    return {
        "video_id": video_id,
        "title": title,
        "url": f"https://example.com/{video_id}",
        "filename": f"{video_id}.mp4",
        "filepath": f"/tmp/media/{video_id}.mp4",
        "quality": 480,
        "duration": 10,
        "file_size": 1024,
        "downloaded_at": timestamp,
    }


def _inject(records):
    history._save(records)


def _history_path(data_dir):
    return data_dir / "history.json"


def _ids(history_list):
    return [r["video_id"] for r in history_list]


def test_list_returns_newest_first(data_dir):
    _inject([
        _make_record("AAA", "Oldest", "2026-01-01T10:00:00+00:00"),
        _make_record("CCC", "Newest", "2026-03-01T10:00:00+00:00"),
        _make_record("BBB", "Middle", "2026-02-01T10:00:00+00:00"),
    ])

    listing = history.get_download_history()
    assert _ids(listing) == ["CCC", "BBB", "AAA"]


def test_find_download_by_video_id(data_dir):
    _inject([
        _make_record("AAA", "First",  "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Second", "2026-02-01T10:00:00+00:00"),
    ])

    record = history.find_download("BBB")
    assert record["video_id"] == "BBB"
    assert record["title"] == "Second"


def test_find_download_returns_most_recent(data_dir):
    """find_download must return the most recent record when multiple exist."""
    _inject([
        _make_record("AAA", "old", "2026-01-01T10:00:00+00:00"),
        _make_record("AAA", "new", "2026-03-01T10:00:00+00:00"),
    ])

    record = history.find_download("AAA")
    assert record["title"] == "new"


def test_find_download_missing_returns_none(data_dir):
    _inject([
        _make_record("AAA", "First", "2026-01-01T10:00:00+00:00"),
    ])

    assert history.find_download("NOPE") is None


def test_find_multiple_downloads(data_dir):
    _inject([
        _make_record("AAA", "One", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Two", "2026-02-01T10:00:00+00:00"),
    ])

    assert _ids(history.find_downloads("AAA")) == ["AAA"]
    assert _ids(history.find_downloads("ZZZ")) == []


def test_remove_one_record(data_dir):
    _inject([
        _make_record("AAA", "One", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Two", "2026-02-01T10:00:00+00:00"),
    ])

    removed = history.remove_download("AAA")
    assert removed["video_id"] == "AAA"
    assert history.find_download("AAA") is None
    assert history.find_download("BBB") is not None
    assert history.count_history() == 1


def test_remove_nonexistent_is_safe(data_dir):
    _inject([
        _make_record("AAA", "One", "2026-01-01T10:00:00+00:00"),
    ])

    assert history.remove_download("NOPE") is None
    assert history.count_history() == 1


def test_clear_history(data_dir):
    _inject([
        _make_record("AAA", "One", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Two", "2026-02-01T10:00:00+00:00"),
    ])

    history.clear_history()
    assert history.count_history() == 0
    assert history.get_download_history() == []


def test_media_files_never_deleted_by_history_ops(data_dir, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    video_file = media / "My Video.mp4"
    video_file.write_bytes(b"real media bytes")

    _inject([
        _make_record("AAA", "My Video", "2026-01-01T10:00:00+00:00"),
    ])

    history.remove_download("AAA")
    history.clear_history()

    assert video_file.exists()
    assert video_file.read_bytes() == b"real media bytes"


def test_history_remains_valid_after_mutations(data_dir):
    _inject([
        _make_record("AAA", "A", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "B", "2026-02-01T10:00:00+00:00"),
        _make_record("CCC", "C", "2026-03-01T10:00:00+00:00"),
    ])

    history.remove_download("BBB")
    history._save(history._load() + [
        _make_record("DDD", "D", "2026-04-01T10:00:00+00:00"),
    ])

    raw = history._load()
    assert _ids(raw) == ["AAA", "CCC", "DDD"]
    assert history.count_history() == 3

    # confirm persisted file has valid structure
    history_path = _history_path(data_dir)
    payload = history_path.read_text()
    assert '"version": 1' in payload
    assert '"downloads"' in payload


def test_count_history(data_dir):
    assert history.count_history() == 0
    _inject([
        _make_record("AAA", "One", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Two", "2026-02-01T10:00:00+00:00"),
    ])
    assert history.count_history() == 2


# --------------------------------------------------------------------------- #
# Media-type-aware uniqueness (video_id + media_type)
# --------------------------------------------------------------------------- #


def _record_meta(video_id, media_type="video"):
    return history.record_download(
        video_id=video_id,
        title="T",
        url=f"https://example.com/{video_id}",
        filename=f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}",
        filepath=f"/tmp/media/{video_id}",
        quality=None if media_type == "audio" else 480,
        duration=10,
        file_size=1024,
        media_type=media_type,
    )


def test_record_video_creates_video_record(data_dir):
    record = _record_meta("AAA", "video")
    stored = history.find_download("AAA")
    assert stored["video_id"] == "AAA"
    assert stored["media_type"] == "video"
    assert record["media_type"] == "video"


def test_record_audio_same_video_creates_second_record(data_dir):
    _record_meta("AAA", "video")
    _record_meta("AAA", "audio")

    downloads = history.find_downloads("AAA")
    assert len(downloads) == 2
    types = sorted(r["media_type"] for r in downloads)
    assert types == ["audio", "video"]


def test_record_same_video_updates_existing_video_record(data_dir):
    _record_meta("AAA", "video")
    _record_meta("AAA", "video")

    downloads = history.find_downloads("AAA")
    assert len(downloads) == 1
    assert downloads[0]["media_type"] == "video"
    assert history.count_history() == 1


def test_record_same_audio_updates_existing_audio_record(data_dir):
    _record_meta("AAA", "audio")
    _record_meta("AAA", "audio")

    downloads = history.find_downloads("AAA")
    assert len(downloads) == 1
    assert downloads[0]["media_type"] == "audio"
    assert downloads[0]["quality"] is None
    assert history.count_history() == 1


def test_legacy_record_without_media_type_matches_video_update(data_dir):
    """A legacy record with no media_type is treated as 'video' for updates."""
    _inject([
        {
            "video_id": "AAA",
            "title": "old",
            "url": "https://example.com/AAA",
            "filename": "AAA.mp4",
            "filepath": "/tmp/media/AAA.mp4",
            "quality": 480,
            "duration": 10,
            "file_size": 1024,
            "downloaded_at": "2026-01-01T10:00:00+00:00",
        }
    ])
    _record_meta("AAA", "video")

    downloads = history.find_downloads("AAA")
    assert len(downloads) == 1
    assert downloads[0]["media_type"] == "video"


def test_legacy_record_without_media_type_allows_separate_audio(data_dir):
    """A legacy record without media_type does not block a fresh audio record."""
    _inject([
        {
            "video_id": "AAA",
            "title": "T",
            "url": "https://example.com/AAA",
            "filename": "AAA.mp4",
            "filepath": "/tmp/media/AAA.mp4",
            "quality": 480,
            "duration": 10,
            "file_size": 1024,
            "downloaded_at": "2026-01-01T10:00:00+00:00",
        }
    ])
    _record_meta("AAA", "audio")

    downloads = history.find_downloads("AAA")
    assert len(downloads) == 2
    types = sorted(r.get("media_type", "video") for r in downloads)
    assert types == ["audio", "video"]


def test_remove_download_removes_all_media_type_records(data_dir):
    """Remove must clear every record for a video id (video AND audio), not just one.

    Regression for the release review: with audio and video tracked as separate
    records for the same id, remove used to leave the other type behind.
    """
    _record_meta("AAA", "video")
    _record_meta("AAA", "audio")

    removed = history.remove_download("AAA")
    assert removed is not None
    assert history.find_downloads("AAA") == []
    assert history.count_history() == 0
    # The other video's record is untouched.
    _record_meta("BBB", "video")
    assert history.count_history() == 1
