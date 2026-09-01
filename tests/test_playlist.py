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
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry) or "downloaded")
    cli._process_playlist(info)
    assert [c["id"] for c in calls] == ["v1", "v2", "v3"]
    out = capsys.readouterr().out
    assert "Playlist item 1" in out
    assert "Playlist item 2" in out
    assert "Playlist item 3" in out


def test_process_playlist_sequential_order(monkeypatch):
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry) or "downloaded")
    cli._process_playlist(info)
    assert [c["id"] for c in calls] == ["v1", "v2"]


def test_process_playlist_uses_single_video_pipeline(monkeypatch):
    info = _playlist_info(entries=[_resolved("v1", "One")])
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry) or "downloaded")
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
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry) or "downloaded")
    cli._process_playlist(info)
    assert [c["id"] for c in calls] == ["v1", "v2"]


def test_process_playlist_skips_malformed_entries(monkeypatch, capsys):
    info = _playlist_info(
        title="Mixed",
        entries=[None, "not-a-dict", {}, _resolved("v1", "One"), _resolved("v2", "Two")],
    )
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry) or "downloaded")
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
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry) or "downloaded")
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
        return "downloaded"

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


def _summary(stats):
    assert stats["total"] == stats["downloaded"] + stats["skipped"] + stats["failed"] + stats["unresolved"]
    return stats


def test_playlist_summary_all_downloaded(monkeypatch, capsys):
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two"), _resolved("v3", "Three")])
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry) or "downloaded")
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 3, "downloaded": 3, "skipped": 0, "failed": 0, "unresolved": 0}
    out = capsys.readouterr().out
    assert "Playlist complete" in out
    assert "Total      : 3" in out
    assert "Downloaded : 3" in out
    assert "Skipped    : 0" in out
    assert "Failed     : 0" in out
    assert "Unresolved : 0" in out


def test_playlist_summary_mixed_results(monkeypatch, capsys):
    info = _playlist_info(
        title="Mixed",
        entries=[
            _resolved("d1", "Downloaded"),
            _resolved("s1", "Skipped"),
            _resolved("f1", "Failed"),
            _partial("u1", "Unresolved"),
        ],
    )
    calls = []
    failed = {"f1"}

    def fake_download(entry):
        calls.append(entry["id"])
        if entry["id"] in failed:
            raise DownloadFailure("boom")
        if entry["id"] in ("d1",):
            return "downloaded"
        if entry["id"] in ("s1",):
            return "skipped"
        return "failed"

    def fake_resolve(entry):
        if entry.get("id") == "u1":
            return None
        return entry

    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    monkeypatch.setattr(cli, "_download_video", fake_download)
    stats = _summary(cli._process_playlist(info))
    assert calls == ["d1", "s1", "f1"]
    assert stats == {"total": 4, "downloaded": 1, "skipped": 1, "failed": 1, "unresolved": 1}
    out = capsys.readouterr().out
    assert "Total      : 4" in out
    assert "Downloaded : 1" in out
    assert "Skipped    : 1" in out
    assert "Failed     : 1" in out
    assert "Unresolved : 1" in out


def test_playlist_summary_empty(monkeypatch, capsys):
    info = _playlist_info(title="Empty", entries=[])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "unresolved": 0}
    out = capsys.readouterr().out
    assert "Playlist complete" in out
    assert "Total      : 0" in out


def test_playlist_summary_failed_item_does_not_stop_later_items(monkeypatch, capsys):
    info = _playlist_info(entries=[_resolved("f1", "Fail"), _resolved("d1", "Done")])
    calls = []
    attempts = []

    def flaky_download(entry):
        attempts.append(entry["id"])
        if entry["id"] == "f1":
            raise DownloadFailure("boom")
        calls.append(entry["id"])
        return "downloaded"

    monkeypatch.setattr(cli, "_download_video", flaky_download)
    stats = _summary(cli._process_playlist(info))
    assert attempts == ["f1", "d1"]
    assert calls == ["d1"]
    assert stats == {"total": 2, "downloaded": 1, "skipped": 0, "failed": 1, "unresolved": 0}


def test_playlist_summary_cancel_no_completion(monkeypatch, capsys):
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "_download_video", lambda entry: pytest.fail("no download on cancel"))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(sys, "argv", ["downv"])
    cli._run_download()
    out = capsys.readouterr().out
    assert "Download cancelled." in out
    assert "Playlist complete" not in out
    assert "Downloaded :" not in out


def test_playlist_summary_accurate_counts(monkeypatch, capsys):
    info = _playlist_info(
        title="Mixed",
        entries=[
            _resolved("a1", "A"),
            None,
            _resolved("a2", "B"),
            _resolved("a3", "C"),
            "junk",
            _partial("a4", "D"),
        ],
    )
    calls = []

    def fake_resolve(entry):
        if not isinstance(entry, dict) or entry.get("id") == "a4":
            return None
        return entry

    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry["id"]) or "downloaded")
    stats = _summary(cli._process_playlist(info))
    assert calls == ["a1", "a2", "a3"]
    assert stats == {"total": 6, "downloaded": 3, "skipped": 0, "failed": 0, "unresolved": 3}


def test_playlist_summary_lazy_generator_processed_once(monkeypatch, capsys):
    produced = []

    def gen():
        for i in range(3):
            produced.append(i)
            yield _resolved(f"v{i}", f"Title {i}")

    calls = []
    info = {"_type": "playlist", "title": "Lazy", "entries": gen()}
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry["id"]) or "downloaded")
    stats = _summary(cli._process_playlist(info))
    assert produced == [0, 1, 2]
    assert calls == ["v0", "v1", "v2"]
    assert stats == {"total": 3, "downloaded": 3, "skipped": 0, "failed": 0, "unresolved": 0}
    out = capsys.readouterr().out
    assert "Total      : 3" in out


def test_playlist_summary_no_playlist_level_history(monkeypatch, tmp_path, data_dir, capsys):
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])

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
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 2, "downloaded": 2, "skipped": 0, "failed": 0, "unresolved": 0}
    assert history.count_history() == 2
    for record in history.get_download_history():
        assert record["title"] != "My Playlist"


def test_playlist_summary_single_video_unaffected(monkeypatch, tmp_path, data_dir, capsys):
    info = _resolved("v1", "One")
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)

    def fake_download(entry, selected, output_dir):
        history.record_download(
            video_id=entry["id"],
            title=entry["title"],
            url=entry["webpage_url"],
            filename="one.mp4",
            filepath=str(output_dir / "one.mp4"),
            quality=selected.height,
            duration=entry.get("duration"),
            file_size=100,
        )
        return output_dir / "one.mp4"

    monkeypatch.setattr(cli, "download_media", fake_download)
    monkeypatch.setattr("builtins.input", lambda prompt: "https://example.com/watch?v=v1")
    monkeypatch.setattr(sys, "argv", ["downv"])
    cli._run_download()
    assert history.count_history() == 1
    out = capsys.readouterr().out
    assert "✓ Download completed" in out
    assert "Playlist complete" not in out
    assert history.count_history() == 1


def _pipeline_patches(monkeypatch, tmp_path):
    """Hook selection/download so the real `_download_video` runs end to end.

    ``download_media`` is replaced with a stub that writes a real file and
    records history (mirroring the real ``_record``), so the real
    ``find_existing_download`` duplicate detection works against the file.
    """
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fake_download(entry, selected, output_dir):
        fname = f"{entry['id']}.mp4"
        target = output_dir / fname
        target.write_bytes(b"data")
        history.record_download(
            video_id=entry["id"],
            title=entry["title"],
            url=entry["webpage_url"],
            filename=fname,
            filepath=str(target),
            quality=selected.height,
            duration=entry.get("duration"),
            file_size=target.stat().st_size,
        )
        return target

    monkeypatch.setattr(cli, "download_media", fake_download)


def test_playlist_success_creates_exactly_one_history_record(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(entries=[_resolved("v1", "One")])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0, "unresolved": 0}
    assert history.count_history() == 1
    record = history.find_download("v1")
    assert record is not None
    assert record["title"] == "One"
    assert record["video_id"] == "v1"


def test_playlist_multiple_success_creates_one_record_per_video(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two"), _resolved("v3", "Three")])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 3, "downloaded": 3, "skipped": 0, "failed": 0, "unresolved": 0}
    assert history.count_history() == 3
    for vid in ("v1", "v2", "v3"):
        assert history.find_download(vid) is not None


def test_playlist_already_downloaded_item_is_skipped(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    # v1 already has a valid, recorded download on disk.
    target = tmp_path / "v1.mp4"
    target.write_bytes(b"data")
    history.record_download(
        video_id="v1",
        title="One",
        url="https://example.com/watch?v=v1",
        filename="v1.mp4",
        filepath=str(target),
        quality=480,
        duration=60,
        file_size=target.stat().st_size,
    )

    downloads_called = []

    def counting_download(entry, selected, output_dir):
        downloads_called.append(entry["id"])
        return _pipeline_downloaded(entry, selected, output_dir)

    monkeypatch.setattr(cli, "download_media", counting_download)
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 2, "downloaded": 1, "skipped": 1, "failed": 0, "unresolved": 0}
    assert downloads_called == ["v2"]
    assert "✓ Video already downloaded" in capsys.readouterr().out


def _pipeline_downloaded(entry, selected, output_dir):
    fname = f"{entry['id']}.mp4"
    target = output_dir / fname
    target.write_bytes(b"data")
    history.record_download(
        video_id=entry["id"],
        title=entry["title"],
        url=entry["webpage_url"],
        filename=fname,
        filepath=str(target),
        quality=selected.height,
        duration=entry.get("duration"),
        file_size=target.stat().st_size,
    )
    return target


def test_playlist_skipped_does_not_create_new_history_record(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    target = tmp_path / "v1.mp4"
    target.write_bytes(b"data")
    history.record_download(
        video_id="v1",
        title="One",
        url="https://example.com/watch?v=v1",
        filename="v1.mp4",
        filepath=str(target),
        quality=480,
        duration=60,
        file_size=target.stat().st_size,
    )
    before = len(history.find_downloads("v1"))  # 1 existing record for v1
    _summary(cli._process_playlist(info))
    assert history.count_history() == 2  # v1 (unchanged) + newly downloaded v2
    assert len(history.find_downloads("v1")) == before
    assert "✓ Video already downloaded" in capsys.readouterr().out


def test_playlist_skipped_does_not_modify_existing_history_record(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    target = tmp_path / "v1.mp4"
    target.write_bytes(b"data")
    history.record_download(
        video_id="v1",
        title="Original",
        url="https://example.com/watch?v=v1",
        filename="v1.mp4",
        filepath=str(target),
        quality=480,
        duration=60,
        file_size=target.stat().st_size,
    )
    original = history.find_download("v1")
    _summary(cli._process_playlist(info))
    after = history.find_download("v1")
    assert after == original
    assert after["title"] == "Original"
    assert target.read_bytes() == b"data"


def test_playlist_duplicate_video_creates_no_duplicate_history(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v1", "One")])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 2, "downloaded": 1, "skipped": 1, "failed": 0, "unresolved": 0}
    incoming = [r for r in history.get_download_history() if r["video_id"] == "v1"]
    assert len(incoming) == 1


def test_playlist_failed_creates_no_success_history_record(monkeypatch, tmp_path, data_dir, capsys):
    def failing_download(entry, selected, output_dir):
        raise DownloadFailure("boom")

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "download_media", failing_download)
    info = _playlist_info(entries=[_resolved("v1", "One")])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 1, "downloaded": 0, "skipped": 0, "failed": 1, "unresolved": 0}
    assert history.count_history() == 0


def test_playlist_unresolved_creates_no_history_record(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(entries=[_partial("u1", "Unresolved"), _resolved("v1", "One")])

    def fake_resolve(entry):
        if entry.get("id") == "u1":
            return None
        return entry

    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 2, "downloaded": 1, "skipped": 0, "failed": 0, "unresolved": 1}
    assert history.find_download("u1") is None
    assert history.count_history() == 1


def test_playlist_continues_after_skipped_and_failed(monkeypatch, tmp_path, data_dir, capsys):
    def flaky_download(entry, selected, output_dir):
        if entry["id"] == "v2":
            raise DownloadFailure("boom")
        return _pipeline_downloaded(entry, selected, output_dir)

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "download_media", flaky_download)
    info = _playlist_info(
        entries=[_resolved("v1", "One"), _resolved("v2", "Two"), _resolved("v3", "Three")]
    )
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 3, "downloaded": 2, "skipped": 0, "failed": 1, "unresolved": 0}
    assert history.count_history() == 2
    assert history.find_download("v1") is not None
    assert history.find_download("v2") is None
    assert history.find_download("v3") is not None


def test_playlist_no_playlist_level_history_record(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    _summary(cli._process_playlist(info))
    assert history.count_history() == 2
    for record in history.get_download_history():
        assert record["title"] != "My Playlist"


def test_playlist_legacy_existing_record_skipped(monkeypatch, tmp_path, data_dir, capsys):
    _pipeline_patches(monkeypatch, tmp_path)
    target = tmp_path / "legacy.mp4"
    target.write_bytes(b"data")
    history.record_download(
        video_id="v1",
        title="Legacy",
        url="https://example.com/watch?v=v1",
        filename="legacy.mp4",
        filepath=str(target),
        quality=480,
        duration=60,
        file_size=target.stat().st_size,
    )
    info = _playlist_info(entries=[_resolved("v1", "One")])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 1, "downloaded": 0, "skipped": 1, "failed": 0, "unresolved": 0}
    assert history.count_history() == 1
    assert history.find_download("v1")["title"] == "Legacy"


def _url_only_entry(vid, title):
    return {
        "id": vid,
        "title": title,
        "url": f"https://example.com/watch?v={vid}",
        "formats": [{"format_id": "0", "height": 480, "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000}],
        "duration": 60,
    }


def test_resolve_playlist_entry_forwards_url_only_format_entry(monkeypatch):
    """A resolved entry with only a ``url`` must still yield a usable download URL."""
    entry = _url_only_entry("v1", "One")
    resolved = cli._resolve_playlist_entry(entry)
    assert resolved is not None
    assert resolved.get("webpage_url")
    assert resolved.get("original_url")


def test_playlist_url_only_resolved_entry_downloads(monkeypatch, tmp_path, data_dir, capsys):
    """Regression: a fully-resolved entry whose only URL field is ``url`` must
    download successfully instead of failing for lack of a usable URL."""
    _pipeline_patches(monkeypatch, tmp_path)
    info = _playlist_info(entries=[_url_only_entry("v1", "One")])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0, "unresolved": 0}
    assert history.count_history() == 1
    assert "✓ Download completed" in capsys.readouterr().out


def test_resolve_playlist_entry_resolved_entry_unchanged_when_webpage_url_present(monkeypatch):
    entry = _resolved("v1", "One")
    resolved = cli._resolve_playlist_entry(entry)
    assert resolved is entry  # reused as-is, not copied/mutated
    assert resolved.get("webpage_url")


def test_playlist_resolved_entry_with_no_url_is_unresolved(monkeypatch, capsys):
    """A fully-resolved entry (has formats) but no URL at all must be unresolved."""
    entry = {"id": "v1", "title": "One", "formats": [{}]}
    assert cli._resolve_playlist_entry(entry) is None
    info = _playlist_info(title="No Url", entries=[entry])
    monkeypatch.setattr(cli, "_download_video", lambda e: pytest.fail("must not download"))
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 1, "downloaded": 0, "skipped": 0, "failed": 0, "unresolved": 1}


def test_playlist_multiple_failures_all_counted_and_continue(monkeypatch, tmp_path, data_dir, capsys):
    """Several failing items are all counted Failed and later items still run."""

    def flaky(entry, selected, output_dir):
        raise DownloadFailure(f"boom {entry['id']}")

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "download_media", flaky)
    info = _playlist_info(entries=[_resolved("a", "A"), _resolved("b", "B"), _resolved("c", "C")])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 3, "downloaded": 0, "skipped": 0, "failed": 3, "unresolved": 0}
    assert history.count_history() == 0
    out = capsys.readouterr().out
    assert "Playlist item 1" in out and "Playlist item 2" in out and "Playlist item 3" in out


def test_playlist_non_download_failure_exception_is_failed_and_continues(monkeypatch, tmp_path, data_dir, capsys):
    """An unexpected (non-DownloadFailure) exception in an item is counted as
    Failed and does not stop later items."""

    def unexpected(entry, selected, output_dir):
        if entry["id"] == "a":
            raise ValueError("unexpected")
        return _pipeline_downloaded(entry, selected, output_dir)

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "download_media", unexpected)
    info = _playlist_info(entries=[_resolved("a", "A"), _resolved("b", "B")])
    stats = _summary(cli._process_playlist(info))
    assert stats == {"total": 2, "downloaded": 1, "skipped": 0, "failed": 1, "unresolved": 0}
    assert history.find_download("b") is not None
    assert history.find_download("a") is None
    assert "unexpected" in capsys.readouterr().out


def test_playlist_summary_invariant_every_category(monkeypatch, tmp_path, data_dir, capsys):
    """A single playlist producing every category must satisfy
    Total == Downloaded + Skipped + Failed + Unresolved."""
    entry_a = _resolved("a", "A")
    entry_b = _resolved("b", "B")
    entry_c = _resolved("c", "C")
    entry_d = _partial("d", "D")
    info = _playlist_info(
        title="All",
        entries=[entry_a, entry_a, entry_b, entry_c, entry_d, None],
    )
    failed = {"c"}

    def fake_download(entry, selected, output_dir):
        if entry["id"] in failed:
            raise DownloadFailure("boom")
        return _pipeline_downloaded(entry, selected, output_dir)

    def fake_resolve(entry):
        if not isinstance(entry, dict) or entry.get("id") == "d":
            return None
        return entry

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "download_media", fake_download)
    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    stats = _summary(cli._process_playlist(info))
    # a (twice -> 1 download + 1 skip), b download, c failed, d unresolved, None unresolved
    assert stats == {"total": 6, "downloaded": 2, "skipped": 1, "failed": 1, "unresolved": 2}
