"""Unit tests for Phase 6A playlist detection & confirmation."""

import sys
from pathlib import Path

import pytest

from downv import cli, history
from downv.downloader import DownloadFailure
from downv.extractor import MediaInfoError
from downv.formats import SelectedMediaFormat


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "get_data_directory", lambda: tmp_path)
    return tmp_path


def _playlist_info(title="My Playlist", uploader="Example Channel", count=12, entries=None):
    info = {"_type": "playlist", "title": title, "entries": entries if entries is not None else [{}] * count}
    if uploader:
        info["uploader"] = uploader
    return info


def _resolved(vid, title):
    """A fully-resolved playlist entry (carries ``formats``)."""
    return {
        "id": vid,
        "title": title,
        "webpage_url": f"https://example.com/watch?v={vid}",
        "url": f"https://example.com/watch?v={vid}",
        "formats": [{"format_id": "0", "height": 480, "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000}],
        "duration": 60,
    }


def _partial(vid, title):
    """A partially-resolved playlist entry (URL but no formats)."""
    return {"id": vid, "title": title, "webpage_url": f"https://example.com/watch?v={vid}"}


def _selected():
    return SelectedMediaFormat(480, "0", None, 1000)


def test_safe_playlist_count_prefers_playlist_count():
    info = {"playlist_count": 7, "entries": [{}] * 3}
    assert cli._safe_playlist_count(info) == 7


def test_safe_playlist_count_falls_back_to_len():
    info = {"entries": [{}] * 5}
    assert cli._safe_playlist_count(info) == 5


def test_safe_playlist_count_lazy_entries_returns_none(monkeypatch):
    class _Lazy:
        def __iter__(self):
            return iter([{}, {}])

        def __len__(self):
            raise TypeError("no len")

    info = {"entries": _Lazy()}
    assert cli._safe_playlist_count(info) is None


def test_safe_playlist_count_missing_returns_none():
    assert cli._safe_playlist_count({}) is None


def test_describe_playlist_uses_available_metadata():
    info = _playlist_info(title="My Playlist", uploader="Example Channel", count=12)
    title, uploader, count = cli._describe_playlist(info)
    assert title == "My Playlist"
    assert uploader == "Example Channel"
    assert count == 12


def test_describe_playlist_partial_metadata_does_not_crash():
    info = {"_type": "playlist"}
    title, uploader, count = cli._describe_playlist(info)
    assert title == "Untitled playlist"
    assert uploader == "Unknown"
    assert count is None


def test_handle_playlist_confirmed(monkeypatch, capsys):
    info = _playlist_info(count=12)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "y")
    assert cli._handle_playlist(info) is True
    out = capsys.readouterr().out
    assert "Playlist detected" in out
    assert "Title    : My Playlist" in out
    assert "Uploader : Example Channel" in out
    assert "Videos   : 12" in out
    assert prompts and "Download all 12 videos? [y/N]:" in prompts[0]


def test_handle_playlist_declined(monkeypatch, capsys):
    info = _playlist_info(count=12)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    assert cli._handle_playlist(info) is False
    assert prompts and "Download all 12 videos? [y/N]:" in prompts[0]
    out = capsys.readouterr().out
    assert "Playlist detected" in out


def test_handle_playlist_declined_default_empty(monkeypatch, capsys):
    info = _playlist_info(count=12)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert cli._handle_playlist(info) is False


def test_handle_playlist_unknown_count_uses_generic_prompt(monkeypatch, capsys):
    info = {"_type": "playlist", "title": "No Count", "entries": object()}
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    cli._handle_playlist(info)
    out = capsys.readouterr().out
    assert "Videos   : ?" in out
    assert prompts and "Download all videos? [y/N]:" in prompts[0]


def test_run_download_playlist_confirmed_no_media_download(monkeypatch, capsys):
    """Confirmation must not trigger any download/show single-video path."""
    info = _playlist_info(title="My Playlist", uploader="Example Channel", count=12)
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "download_media", lambda *a, **k: pytest.fail("download called"))
    monkeypatch.setattr(cli, "find_existing_download", lambda info: None)
    monkeypatch.setattr(cli, "select_formats", lambda info: {})
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert "Playlist detected" in out
    assert "My Playlist" in out
    assert "Videos   : 12" in out
    assert "✓ Playlist confirmed" in out


def test_run_download_playlist_declined_no_download(monkeypatch, capsys):
    info = _playlist_info(title="My Playlist", count=12)
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "download_media", lambda *a, **k: pytest.fail("download called"))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert "Playlist detected" in out
    assert "Download cancelled." in out


def test_run_download_empty_playlist_is_reported(monkeypatch, capsys):
    """An empty / no-usable-entries playlist is safely reported without crashing."""
    info = _playlist_info(title="Empty List", uploader="Channel", count=0, entries=[])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "download_media", lambda *a, **k: pytest.fail("download called"))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert "Playlist detected" in out
    assert "Empty List" in out
    assert "Videos   : 0" in out


# --- Phase 6B: playlist per-video download loop ---


def test_process_playlist_processes_each_entry(monkeypatch, capsys):
    info = _playlist_info(
        title="My Playlist",
        entries=[_resolved("v1", "One"), _resolved("v2", "Two"), _resolved("v3", "Three")],
    )
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry))
    cli._process_playlist(info)
    assert [c["id"] for c in calls] == ["v1", "v2", "v3"]
    out = capsys.readouterr().out
    assert "Playlist item 1" in out
    assert "Playlist item 2" in out
    assert "Playlist item 3" in out


def test_process_playlist_sequential_order(monkeypatch):
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry))
    cli._process_playlist(info)
    assert [c["id"] for c in calls] == ["v1", "v2"]


def test_process_playlist_uses_single_video_pipeline(monkeypatch):
    info = _playlist_info(entries=[_resolved("v1", "One")])
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry))
    cli._process_playlist(info)
    assert len(calls) == 1
    assert calls[0]["title"] == "One"
    assert "formats" in calls[0]


def test_download_video_uses_format_selection(monkeypatch, tmp_path, capsys):
    info = _resolved("v1", "One")
    selected = _selected()
    seen_formats = []
    monkeypatch.setattr(cli, "select_formats", lambda i: seen_formats.append(i) or {480: selected})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    downloaded = []
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: downloaded.append((i, s, o)) or (o / "x.mp4"))
    cli._download_video(info)
    assert seen_formats == [info]
    assert len(downloaded) == 1
    assert downloaded[0][1] == selected
    assert downloaded[0][2] == tmp_path


def test_download_video_uses_duplicate_detection(monkeypatch, tmp_path, data_dir, capsys):
    info = _resolved("v1", "One")
    existing_path = tmp_path / "existing.mp4"
    existing_path.write_bytes(b"x")
    history.record_download(
        video_id="v1",
        title="One",
        url="https://example.com/watch?v=v1",
        filename="existing.mp4",
        filepath=str(existing_path),
        quality=480,
        duration=60,
        file_size=1,
    )
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: existing_path)
    monkeypatch.setattr(cli, "download_media", lambda *a, **k: pytest.fail("should not download"))
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    cli._download_video(info)
    out = capsys.readouterr().out
    assert "✓ Video already downloaded" in out
    assert "✓ Download completed" not in out


def test_download_video_records_history_on_success(monkeypatch, tmp_path, data_dir, capsys):
    info = _resolved("v1", "One")

    def fake_download(info, selected, output_dir):
        history.record_download(
            video_id=info["id"],
            title=info["title"],
            url=info["webpage_url"],
            filename="one.mp4",
            filepath=str(output_dir / "one.mp4"),
            quality=selected.height,
            duration=info.get("duration"),
            file_size=100,
        )
        return output_dir / "one.mp4"

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "download_media", fake_download)
    cli._download_video(info)
    assert history.count_history() == 1
    record = history.find_download("v1")
    assert record["title"] == "One"


def test_process_playlist_lazy_generator_entries(monkeypatch):
    def gen():
        yield _resolved("v1", "One")
        yield _resolved("v2", "Two")

    info = {"_type": "playlist", "title": "Lazy", "entries": gen()}
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry))
    cli._process_playlist(info)
    assert [c["id"] for c in calls] == ["v1", "v2"]


def test_process_playlist_skips_malformed_entries(monkeypatch, capsys):
    info = _playlist_info(
        title="Mixed",
        entries=[None, "not-a-dict", {}, _resolved("v1", "One"), _resolved("v2", "Two")],
    )
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry))
    cli._process_playlist(info)
    assert [c["id"] for c in calls] == ["v1", "v2"]


def test_process_playlist_failed_resolution_continues(monkeypatch, capsys):
    info = _playlist_info(
        title="Mixed",
        entries=[_partial("bad", "Bad One"), _resolved("good", "Good One")],
    )
    calls = []

    def fake_resolve(entry):
        if entry.get("id") == "bad":
            return None
        return entry

    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry))
    cli._process_playlist(info)
    assert [c["id"] for c in calls] == ["good"]
    out = capsys.readouterr().out
    assert "✗ Could not resolve playlist item 1." in out
    assert "Playlist item 2" in out


def test_process_playlist_failed_download_continues(monkeypatch, capsys):
    info = _playlist_info(title="Mixed", entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    calls = []
    attempts = []

    def flaky_download(info_entry):
        attempts.append(info_entry["id"])
        if info_entry["id"] == "v1":
            raise DownloadFailure("boom")
        calls.append(info_entry)

    monkeypatch.setattr(cli, "_download_video", flaky_download)
    cli._process_playlist(info)
    assert attempts == ["v1", "v2"]
    assert [c["id"] for c in calls] == ["v2"]


def test_run_download_cancel_performs_zero_downloads(monkeypatch, capsys):
    info = _playlist_info(title="My Playlist", count=2, entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "_download_video", lambda entry: pytest.fail("no download on cancel"))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(sys, "argv", ["downv"])
    cli._run_download()
    out = capsys.readouterr().out
    assert "Download cancelled." in out


def test_single_video_flow_unchanged(monkeypatch, tmp_path, capsys):
    info = _resolved("v1", "One")
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    downloaded = []
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: downloaded.append((i, s, o)) or (o / "x.mp4"))
    monkeypatch.setattr("builtins.input", lambda prompt: "https://example.com/watch?v=v1")
    monkeypatch.setattr(sys, "argv", ["downv"])
    cli._run_download()
    assert len(downloaded) == 1
    out = capsys.readouterr().out
    assert "✓ Download completed" in out


def test_no_playlist_level_history_record(monkeypatch, tmp_path, data_dir, capsys):
    info = _playlist_info(
        title="My Playlist",
        entries=[_resolved("v1", "One"), _resolved("v2", "Two")],
    )

    def fake_download(entry, selected, output_dir):
        history.record_download(
            video_id=entry["id"],
            title=entry["title"],
            url=entry["webpage_url"],
            filename=f"{entry['id']}.mp4",
            filepath=str(output_dir / f"{entry['id']}.mp4"),
            quality=selected.height,
            duration=entry.get("duration"),
            file_size=100,
        )
        return output_dir / f"{entry['id']}.mp4"

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "download_media", fake_download)
    cli._process_playlist(info)

    assert history.count_history() == 2
    for record in history.get_download_history():
        assert record["video_id"] in ("v1", "v2")
        assert record["title"] != "My Playlist"


def test_playlist_orchestration_does_not_touch_media(monkeypatch, tmp_path, capsys):
    media = tmp_path / "beat.mp4"
    media.write_bytes(b"original-content")
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One")])

    monkeypatch.setattr(cli, "_download_video", lambda entry: None)
    cli._process_playlist(info)

    assert media.exists()
    assert media.read_bytes() == b"original-content"
