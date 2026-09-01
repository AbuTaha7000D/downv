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
    # Answer the confirmation prompt "y" but decline any post-run retry prompt
    # (the fake playlist has no resolvable items, which now offers a retry).
    monkeypatch.setattr("builtins.input", lambda prompt: "n" if "Retry" in prompt else "y")
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


# ---------------------------------------------------------------------------
# Phase 6F: Playlist-wide quality selection & playlist directory organisation
# ---------------------------------------------------------------------------

def test_playlist_dir_name_sanitizes_safely():
    assert cli.playlist_dir_name("My Playlist") == "My_Playlist"
    assert cli.playlist_dir_name("A/B:C*D") != "A/B:C*D"  # separates path/file chars
    assert not any(c in cli.playlist_dir_name("A/B:C*D") for c in "/:*")
    assert cli.playlist_dir_name("") == "Playlist"
    assert cli.playlist_dir_name(None) == "Playlist"
    assert cli.playlist_dir_name("   ") == "Playlist"
    assert cli.playlist_dir_name("..") == "Playlist"


def test_playlist_dir_name_does_not_mutate_title():
    title = "Hi/There"
    cli.playlist_dir_name(title)
    assert title == "Hi/There"


def test_playlist_dir_name_truncates_long_titles(monkeypatch):
    long_title = "X" * 500
    name = cli.playlist_dir_name(long_title)
    assert len(name) <= cli._MAX_PLAYLIST_DIR_NAME


def test_playlist_output_dir_lives_under_default(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    target = cli._playlist_output_dir("My Playlist")
    assert target == tmp_path / "My_Playlist"
    assert target.exists()
    assert target.is_dir()


def test_playlist_output_dir_exists_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    (tmp_path / "My_Playlist").mkdir()
    target = cli._playlist_output_dir("My Playlist")
    assert target == tmp_path / "My_Playlist"
    assert target.exists()


def test_aggregate_playlist_qualities_sums_across_items():
    per_item = [
        {"resolved": {"id": "a"}, "qualities": {480: SelectedMediaFormat(480, "0", None, 1000)}},
        {"resolved": {"id": "b"}, "qualities": {480: SelectedMediaFormat(480, "0", None, 2000)}},
        {"resolved": {"id": "c"}, "qualities": {480: SelectedMediaFormat(480, "0", None, 3000)}},
    ]
    agg = cli._aggregate_playlist_qualities(per_item)
    assert set(agg) == {480}
    assert agg[480].size_bytes == 6000
    assert agg[480].height == 480


def test_aggregate_playlist_qualities_ignores_unresolved():
    per_item = [
        {"resolved": {"id": "a"}, "qualities": {480: _selected()}},
        {"resolved": None, "qualities": {}},
        {"resolved": {"id": "b"}, "qualities": {480: _selected()}},
    ]
    agg = cli._aggregate_playlist_qualities(per_item)
    assert set(agg) == {480}
    assert agg[480].size_bytes == 2000


def test_aggregate_playlist_qualities_unknown_when_any_unknown():
    per_item = [
        {"resolved": {"id": "a"}, "qualities": {480: SelectedMediaFormat(480, "0", None, 1000)}},
        {"resolved": {"id": "b"}, "qualities": {480: SelectedMediaFormat(480, "0", None, None)}},
    ]
    agg = cli._aggregate_playlist_qualities(per_item)
    assert agg[480].size_bytes is None


def test_choose_playlist_quality_returns_none_when_empty(monkeypatch):
    monkeypatch.setattr(cli, "_aggregate_playlist_qualities", lambda per_item: {})
    assert cli._choose_playlist_quality([]) is None


def test_choose_playlist_quality_shows_one_menu(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "_aggregate_playlist_qualities",
        lambda per_item: {480: SelectedMediaFormat(480, "", None, 3000)},
    )
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    height = cli._choose_playlist_quality([
        {"resolved": {"id": "a"}, "qualities": {480: _selected()}},
        {"resolved": {"id": "b"}, "qualities": {480: _selected()}},
    ])
    assert height == 480
    assert "Playlist quality" in capsys.readouterr().out


def test_choose_playlist_quality_uses_total_not_first_item(monkeypatch, capsys):
    """The menu must present the aggregated playlist total, not a single item's size."""
    received = {}
    monkeypatch.setattr(
        cli, "_aggregate_playlist_qualities",
        lambda per_item: {480: SelectedMediaFormat(480, "", None, 9000)},
    )
    monkeypatch.setattr(cli, "select_quality", lambda q: received.update(q) or q[480])
    height = cli._choose_playlist_quality([
        {"resolved": {"id": "a"}, "qualities": {480: _selected()}},
        {"resolved": {"id": "b"}, "qualities": {480: _selected()}},
        {"resolved": {"id": "c"}, "qualities": {480: _selected()}},
    ])
    assert height == 480
    assert received[480].size_bytes == 9000  # the summed playlist total
    assert "Playlist quality" in capsys.readouterr().out


def test_plan_playlist_resolves_and_aggregates_once(monkeypatch):
    """Planning resolves each entry and computes each item's formats exactly once."""
    resolutions = []
    format_calls = []

    def fake_resolve(entry):
        resolutions.append(entry["id"])
        return {"id": entry["id"]}

    def fake_select(info):
        format_calls.append(info["id"])
        return {480: _selected()}

    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    monkeypatch.setattr(cli, "select_formats", fake_select)
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])

    info = {"entries": [{"id": "v1"}, {"id": "v2"}]}
    height, per_item = cli._plan_playlist(info)

    assert height == 480
    assert resolutions == ["v1", "v2"]
    assert format_calls == ["v1", "v2"]
    assert [p["resolved"]["id"] for p in per_item] == ["v1", "v2"]
    assert all(set(p["qualities"]) == {480} for p in per_item)


def test_process_playlist_uses_playlist_dir_and_preset_quality(monkeypatch, tmp_path, capsys):
    """When a height + playlist dir are passed, items use that dir and never re-prompt."""
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    pdir = tmp_path / "My_Playlist"
    pdir.mkdir()

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected(), 720: _selected()})
    monkeypatch.setattr(cli, "download_media", lambda info, sel, out: (out / f"{info['id']}.mp4").write_bytes(b"d"))
    monkeypatch.setattr(cli, "find_existing_download", lambda info: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    cli._process_playlist(info, chosen_height=720, playlist_dir=pdir)
    assert (pdir / "v1.mp4").exists()
    assert (pdir / "v2.mp4").exists()


def test_process_playlist_missing_quality_marks_failed(monkeypatch, tmp_path, capsys):
    """A selected height unavailable for an item must not download a different quality."""
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    pdir = tmp_path / "My_Playlist"
    pdir.mkdir()

    def fake_select(i):
        return {480: _selected()}  # neither item offers the chosen 720p

    monkeypatch.setattr(cli, "select_formats", fake_select)
    monkeypatch.setattr(cli, "download_media", lambda info, sel, out: pytest.fail("no download on missing quality"))
    monkeypatch.setattr(cli, "find_existing_download", lambda info: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    stats = _summary(cli._process_playlist(info, chosen_height=720, playlist_dir=pdir))
    assert stats == {"total": 2, "downloaded": 0, "skipped": 0, "failed": 2, "unresolved": 0}
    assert "not available" in capsys.readouterr().out


def test_run_download_playlist_writes_into_playlist_dir(monkeypatch, tmp_path, data_dir, capsys):
    """End-to-end: confirmation -> one quality menu -> files in <Playlist>/."""
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda info: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fake_download(entry, selected, output_dir):
        target = output_dir / f"{entry['id']}.mp4"
        target.write_bytes(b"d")
        history.record_download(
            video_id=entry["id"], title=entry["title"], url=entry["webpage_url"],
            filename=f"{entry['id']}.mp4", filepath=str(target),
            quality=selected.height, duration=entry.get("duration"), file_size=1,
        )
        return target

    monkeypatch.setattr(cli, "download_media", fake_download)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out

    assert "Playlist quality" in out
    assert "✓ Playlist confirmed" in out
    assert (tmp_path / "My_Playlist").exists()
    assert (tmp_path / "My_Playlist" / "v1.mp4").exists()
    assert (tmp_path / "My_Playlist" / "v2.mp4").exists()
    assert "Playlist complete" in out
    # Files must NOT land at the top level
    assert not (tmp_path / "v1.mp4").exists()

    # History records must point into the playlist directory
    rec = history.find_download("v1")
    assert rec is not None
    assert str(Path(rec["filepath"]).parent) == str(tmp_path / "My_Playlist")

    # No playlist-level history record was created
    assert history.count_history() == 2


def test_run_download_playlist_quality_menued_once(monkeypatch, tmp_path, capsys):
    """The quality menu must appear exactly once for the whole playlist."""
    calls = []
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: calls.append(1) or q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda info: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda info, sel, out: out / f"{info['id']}.mp4")
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    assert len(calls) == 1
    assert capsys.readouterr().out.count("Playlist quality") == 1


def test_run_download_playlist_select_formats_once_per_item(monkeypatch, tmp_path, capsys):
    """The preset-quality path must compute each item's formats exactly once."""
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    format_calls = []
    monkeypatch.setattr(cli, "select_formats", lambda i: format_calls.append(i["id"]) or {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda info: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda info, sel, out: (out / f"{info['id']}.mp4").write_bytes(b"d"))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    assert format_calls == ["v1", "v2"]  # exactly once per item, planning + download reuse the same map


def test_run_download_playlist_rerun_detects_already_downloaded(monkeypatch, tmp_path, data_dir, capsys):
    """Re-running the confirmed playlist finds prior files via history and skips."""
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    entry = _resolved("v1", "One")
    info = _playlist_info(title="My Playlist", entries=[entry])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    pdir = tmp_path / "My_Playlist"
    pdir.mkdir()
    fpath = pdir / "v1.mp4"
    fpath.write_bytes(b"d")
    history.record_download(
        video_id="v1", title="One", url=entry["webpage_url"],
        filename="v1.mp4", filepath=str(fpath), quality=480, duration=60, file_size=1,
    )

    monkeypatch.setattr(cli, "download_media", lambda *a, **k: pytest.fail("duplicate must skip download"))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert "Playlist complete" in out
    assert "Downloaded : 0" in out
    assert "Skipped    : 1" in out


# ---------------------------------------------------------------------------
# Phase 7 / Step 7.1: Playlist per-item progress indication
# ---------------------------------------------------------------------------

def test_playlist_item_progress_known_count(monkeypatch, capsys):
    """A known playlist count displays ``Playlist item N/total``."""
    info = _playlist_info(
        entries=[_resolved("v1", "One"), _resolved("v2", "Two"), _resolved("v3", "Three")],
    )
    monkeypatch.setattr(cli, "_download_video", lambda entry: "downloaded")
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Playlist item 1/3" in out
    assert "Playlist item 2/3" in out
    assert "Playlist item 3/3" in out


def test_playlist_item_progress_explicit_playlist_count(monkeypatch, capsys):
    """A ``playlist_count`` field is preferred over ``len(entries)`` when present."""
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    info["playlist_count"] = 5
    monkeypatch.setattr(cli, "_download_video", lambda entry: "downloaded")
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Playlist item 1/5" in out
    assert "Playlist item 2/5" in out


def test_playlist_item_progress_unknown_count(monkeypatch, capsys):
    """An unknown count must not fabricate a denominator."""
    class _Lazy:
        def __iter__(self):
            return iter([_resolved("v1", "One"), _resolved("v2", "Two")])

    info = {"_type": "playlist", "title": "Lazy", "entries": _Lazy()}
    monkeypatch.setattr(cli, "_download_video", lambda entry: "downloaded")
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Playlist item 1" in out
    assert "Playlist item 2" in out
    assert "Playlist item 1/" not in out and "Playlist item 2/" not in out


def test_playlist_item_progress_empty_playlist(monkeypatch, capsys):
    """An empty playlist still reports cleanly with no item lines."""
    info = _playlist_info(title="Empty List", entries=[])
    monkeypatch.setattr(cli, "_download_video", lambda entry: "downloaded")
    stats = cli._process_playlist(info)
    out = capsys.readouterr().out
    assert stats["total"] == 0
    assert "Playlist item" not in out
    assert "Playlist complete" in out


def test_playlist_item_progress_advances_across_failures(monkeypatch, capsys):
    """Failed and unresolved items still advance the displayed item counter."""
    info = _playlist_info(
        entries=[_resolved("v1", "One"), _resolved("v2", "Two"), _resolved("v3", "Three")],
    )

    def fake_download(entry, selected, output_dir):
        if entry["id"] == "v2":
            raise DownloadFailure("boom")
        return output_dir / f"{entry['id']}.mp4"

    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "download_media", fake_download)

    def fake_resolve(entry):
        if not isinstance(entry, dict) or entry["id"] == "v3":
            return None
        return entry

    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Playlist item 1/3" in out
    assert "Playlist item 2/3" in out
    assert "Playlist item 3/3" in out


def test_playlist_item_progress_lazy_once(monkeypatch, capsys):
    """A lazy generator is processed exactly once and shows no fabricated total."""
    produced = []

    def gen():
        for i in range(3):
            produced.append(i)
            yield _resolved(f"v{i}", f"Title {i}")

    info = {"_type": "playlist", "title": "Lazy", "entries": gen()}
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry["id"]) or "downloaded")
    stats = cli._process_playlist(info)
    assert produced == [0, 1, 2]
    assert calls == ["v0", "v1", "v2"]
    assert stats["total"] == 3
    out = capsys.readouterr().out
    assert "Playlist item 1/" not in out


def test_playlist_item_progress_summary_unchanged(monkeypatch, capsys):
    """The summary invariant still holds with progress denominated output."""
    info = _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda entry: calls.append(entry["id"]) or "downloaded")
    stats = cli._process_playlist(info)
    assert stats["total"] == stats["downloaded"] + stats["skipped"] + stats["failed"] + stats["unresolved"]
    out = capsys.readouterr().out
    assert "Playlist complete" in out
    assert "Total      : 2" in out
    assert "Downloaded : 2" in out


def test_playlist_item_progress_single_video_unchanged(monkeypatch, tmp_path, capsys):
    """Standalone single-video download output is not prefixed with any item line."""
    info = {"id": "v1", "title": "One", "formats": [{"format_id": "0", "height": 480}]}
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda info_, sel, out: out / "x.mp4")
    cli._download_video(info)
    out = capsys.readouterr().out
    assert "Playlist item" not in out
    assert "✓ Download completed" in out


# ---------------------------------------------------------------------------
# Phase 7 / Step 7.2: Playlist failure reporting
# ---------------------------------------------------------------------------

def _fail_playlist(entries):
    """Build a playlist info dict from a list of (id, title) tuples."""
    return _playlist_info(entries=[_resolved(vid, title) for vid, title in entries])


def _patch_pipeline(monkeypatch, tmp_path, fail_ids):
    """Patch the download pipeline so real _download_video runs; blacklist ids fail."""
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fake_download(i, sel, out):
        if i["id"] in fail_ids:
            raise DownloadFailure("boom")
        return out / f"{i['id']}.mp4"

    monkeypatch.setattr(cli, "download_media", fake_download)


def test_playlist_failure_report_single_failed_item(monkeypatch, tmp_path, capsys):
    """One failed item is reported under Failed items with its title."""
    info = _fail_playlist([("v1", "Alpha"), ("v2", "Beta")])
    _patch_pipeline(monkeypatch, tmp_path, {"v2"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Failed items:" in out
    assert "✗ Beta" in out
    assert "Alpha" not in out.split("Failed items:")[1]


def test_playlist_failure_report_includes_reason(monkeypatch, tmp_path, capsys):
    """A failed item includes a concise failure reason."""
    info = _fail_playlist([("v1", "Alpha")])
    _patch_pipeline(monkeypatch, tmp_path, {"v1"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Reason: boom" in out


def test_playlist_failure_report_multiple_failures(monkeypatch, tmp_path, capsys):
    """Multiple failures are all reported."""
    info = _fail_playlist([("v1", "Alpha"), ("v2", "Beta"), ("v3", "Gamma")])
    _patch_pipeline(monkeypatch, tmp_path, {"v1", "v3"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    section = out.split("Failed items:")[1]
    assert "✗ Alpha" in section
    assert "✗ Beta" not in section
    assert "✗ Gamma" in section


def test_playlist_failure_report_ordering(monkeypatch, tmp_path, capsys):
    """Failures are reported in playlist processing order."""
    info = _fail_playlist([("v1", "Alpha"), ("v2", "Beta"), ("v3", "Gamma")])
    _patch_pipeline(monkeypatch, tmp_path, {"v3", "v1"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    section = out.split("Failed items:")[1]
    assert section.index("✗ Alpha") < section.index("✗ Gamma")


def test_playlist_failure_report_unresolved_not_failed(monkeypatch, tmp_path, capsys):
    """An unresolved item must not appear under Failed items."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), None, _resolved("v2", "Beta")])
    _patch_pipeline(monkeypatch, tmp_path, set())
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Failed items:" not in out
    assert "Unresolved : 1" in out


def test_playlist_failure_report_skipped_not_failed(monkeypatch, tmp_path, capsys, data_dir):
    """A skipped (already downloaded) item must not appear under Failed items."""
    info = _fail_playlist([("v1", "Alpha"), ("v2", "Beta")])
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    match = {"v1": {"video_id": "v1", "title": "Alpha", "filename": "v1.mp4"}}
    monkeypatch.setattr(cli, "find_existing_download", lambda i: match.get(i["id"]))
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    recorded = []
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: recorded.append(i["id"]))
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert recorded == ["v2"]
    assert "Skipped    : 1" in out
    assert "Failed items:" not in out


def test_playlist_failure_report_success_not_failed(monkeypatch, tmp_path, capsys):
    """A successful item must not appear under Failed items."""
    info = _fail_playlist([("v1", "Alpha"), ("v2", "Beta")])
    _patch_pipeline(monkeypatch, tmp_path, set())
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Downloaded : 2" in out
    assert "Failed items:" not in out


def test_playlist_failure_report_missing_title(monkeypatch, tmp_path, capsys):
    """A failed item with no usable title falls back to 'Unknown title' without crashing."""
    untitled = {"id": "v1", "webpage_url": "u1", "url": "u1",
                "formats": [{"format_id": "0", "height": 480}]}
    info = _playlist_info(entries=[untitled, _resolved("v2", "Beta")])
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: (_ for _ in ()).throw(DownloadFailure("x")))
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Failed items:" in out
    assert "Unknown title" in out.split("Failed items:")[1]


def test_playlist_failure_report_counts_correct(monkeypatch, tmp_path, capsys):
    """Summary counts remain correct alongside the failure report."""
    info = _fail_playlist([("v1", "Alpha"), ("v2", "Beta"), ("v3", "Gamma")])
    _patch_pipeline(monkeypatch, tmp_path, {"v2"})
    stats = cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    assert stats["total"] == 3
    assert stats["downloaded"] == 2
    assert stats["failed"] == 1
    assert stats["unresolved"] == 0
    assert stats["total"] == stats["downloaded"] + stats["skipped"] + stats["failed"] + stats["unresolved"]


def test_playlist_failure_report_invariant(monkeypatch, tmp_path, capsys):
    """The Total == Downloaded + Skipped + Failed + Unresolved invariant holds with failures."""
    info = _playlist_info(entries=[_resolved("v1", "A"), None, _resolved("v2", "B")])
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: (_ for _ in ()).throw(DownloadFailure("boom")))
    stats = _summary(cli._process_playlist(info))
    assert stats["total"] == stats["downloaded"] + stats["skipped"] + stats["failed"] + stats["unresolved"]


def test_playlist_failure_report_single_video_unchanged(monkeypatch, tmp_path, capsys):
    """Single-video download output is unchanged and never prints Failed items."""
    info = {"id": "v1", "title": "One", "formats": [{"format_id": "0", "height": 480}]}
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: o / "x.mp4")
    cli._download_video(info)
    out = capsys.readouterr().out
    assert "Failed items:" not in out
    assert "✓ Download completed" in out


def test_playlist_failure_report_lazy_safe(monkeypatch, tmp_path, capsys):
    """Lazy playlist processing stays safe and the generator runs exactly once."""
    produced = []

    def gen():
        for i in range(3):
            produced.append(i)
            yield _resolved(f"v{i}", f"Title {i}")

    info = {"_type": "playlist", "title": "Lazy", "entries": gen()}
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i_, s, o: o)
    stats = cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    assert produced == [0, 1, 2]
    assert stats["total"] == 3
    assert stats["downloaded"] == 3


def test_playlist_failure_report_quality_unavailable(monkeypatch, tmp_path, capsys):
    """A quality-unavailable item is Failed with a concise reason, not unresolved."""

    def fake_select(i):
        if i["id"] == "v1":
            return {720: _selected()}
        return {480: _selected()}

    monkeypatch.setattr(cli, "select_formats", fake_select)
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: o / "x.mp4")

    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Failed     : 1" in out
    assert "Failed items:" in out
    assert "Selected quality not available." in out


# ---------------------------------------------------------------------------
# Phase 7 / Step 7.3: Skipped & Unresolved reporting
# ---------------------------------------------------------------------------

def _patch_pipeline_skips(monkeypatch, tmp_path, skip_ids):
    """Patch pipeline so real _download_video runs; ids in skip_ids are duplicates."""
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: {"dup": True} if i["id"] in skip_ids else None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i, sel, out: out / f"{i['id']}.mp4")


def test_playlist_skipped_report_single_title(monkeypatch, tmp_path, capsys):
    """One skipped item is reported under Skipped items with its title."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v2"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Skipped items:" in out
    assert "  - Beta" in out
    assert "Alpha" not in out.split("Skipped items:")[1]


def test_playlist_skipped_report_reason(monkeypatch, tmp_path, capsys):
    """The skipped reason is displayed."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha")])
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v1"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Reason: Already downloaded." in out


def test_playlist_skipped_report_multiple(monkeypatch, tmp_path, capsys):
    """Multiple skipped items are reported."""
    info = _playlist_info(
        entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta"), _resolved("v3", "Gamma")]
    )
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v1", "v3"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    section = out.split("Skipped items:")[1]
    assert "  - Alpha" in section
    assert "  - Beta" not in section
    assert "  - Gamma" in section


def test_playlist_skipped_report_ordering(monkeypatch, tmp_path, capsys):
    """Skipped items follow playlist order."""
    info = _playlist_info(
        entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta"), _resolved("v3", "Gamma")]
    )
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v3", "v1"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    section = out.split("Skipped items:")[1]
    assert section.index("- Alpha") < section.index("- Gamma")


def test_playlist_unresolved_report_title(monkeypatch, tmp_path, capsys):
    """One unresolved item is reported under Unresolved items."""
    info = _playlist_info(entries=[None])
    monkeypatch.setattr(cli, "_download_video", lambda entry: pytest.fail("must not download"))
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Unresolved items:" in out
    assert "  ? Unknown title" in out


def test_playlist_unresolved_report_reason(monkeypatch, tmp_path, capsys):
    """The unresolved reason is displayed."""
    info = _playlist_info(entries=[None])
    monkeypatch.setattr(cli, "_download_video", lambda entry: pytest.fail("must not download"))
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Reason: Could not resolve video information." in out


def test_playlist_unresolved_report_multiple(monkeypatch, tmp_path, capsys):
    """Multiple unresolved items are reported."""
    info = _playlist_info(entries=[None, None])
    monkeypatch.setattr(cli, "_download_video", lambda entry: pytest.fail("must not download"))
    cli._process_playlist(info)
    out = capsys.readouterr().out
    section = out.split("Unresolved items:")[1]
    assert section.count("? Unknown title") == 2
    assert "Unresolved : 2" in out


def test_playlist_unresolved_report_ordering(monkeypatch, tmp_path, capsys):
    """Unresolved items follow playlist order (titles preserved when available)."""
    info = _playlist_info(entries=[{"title": "First"}, {"title": "Second"}])
    monkeypatch.setattr(cli, "_download_video", lambda entry: pytest.fail("must not download"))
    cli._process_playlist(info)
    out = capsys.readouterr().out
    section = out.split("Unresolved items:")[1]
    assert section.index("? First") < section.index("? Second")


def test_playlist_skipped_not_in_failed(monkeypatch, tmp_path, capsys):
    """Skipped items are not included in the Failed report."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v1"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Failed items:" not in out


def test_playlist_unresolved_not_in_failed(monkeypatch, tmp_path, capsys):
    """Unresolved items are not included in the Failed report."""
    info = _playlist_info(entries=[None, _resolved("v2", "Beta")])
    _patch_pipeline(monkeypatch, tmp_path, set())
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Failed items:" not in out
    assert "Unresolved : 1" in out


def test_playlist_mixed_categories_reported_separately(monkeypatch, tmp_path, capsys):
    """A mixed playlist reports Downloaded/Skipped/Failed/Unresolved separately."""
    info = _playlist_info(
        entries=[
            _resolved("v1", "Alpha"),  # downloaded
            _resolved("v2", "Beta"),   # skipped
            _resolved("v3", "Gamma"),  # failed
            None,                      # unresolved
            _resolved("v5", "Epsilon"),  # downloaded
        ]
    )
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: {"dup": 1} if i["id"] == "v2" else None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fake_download(i, sel, out):
        if i["id"] == "v3":
            raise DownloadFailure("boom")
        return out / f"{i['id']}.mp4"

    monkeypatch.setattr(cli, "download_media", fake_download)
    stats = cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    assert stats == {"total": 5, "downloaded": 2, "skipped": 1, "failed": 1, "unresolved": 1}
    out = capsys.readouterr().out
    assert "Skipped items:" in out and "  - Beta" in out
    assert "Unresolved items:" in out
    assert "Failed items:" in out and "  ✗ Gamma" in out


def test_playlist_unresolved_missing_title_fallback(monkeypatch, tmp_path, capsys):
    """Missing title uses the safe fallback for unresolved items."""
    info = {"_type": "playlist", "title": "P", "entries": ["bad"]}
    monkeypatch.setattr(cli, "_download_video", lambda entry: pytest.fail("must not download"))
    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert "Unknown title" in out.split("Unresolved items:")[1]


def test_playlist_skipped_missing_title_fallback(monkeypatch, tmp_path, capsys):
    """A skipped item with no usable title falls back to 'Unknown title'."""
    untitled = {"id": "v1", "webpage_url": "u1", "url": "u1",
                "formats": [{"format_id": "0", "height": 480}]}
    info = _playlist_info(entries=[untitled])
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v1"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Unknown title" in out.split("Skipped items:")[1]


def test_playlist_skipped_summary_counts(monkeypatch, tmp_path, capsys):
    """Summary counts remain unchanged with skipped items."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v1"})
    stats = cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    assert stats["total"] == 2
    assert stats["downloaded"] == 1
    assert stats["skipped"] == 1
    assert stats["failed"] == 0
    assert stats["unresolved"] == 0


def test_playlist_skipped_unresolved_invariant(monkeypatch, tmp_path, capsys):
    """The summary invariant holds with skipped and unresolved items."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), None, _resolved("v2", "Beta")])
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v1"})
    stats = _summary(cli._process_playlist(info))
    assert stats["total"] == stats["downloaded"] + stats["skipped"] + stats["failed"] + stats["unresolved"]


def test_playlist_no_skipped_or_unresolved_no_sections(monkeypatch, tmp_path, capsys):
    """A playlist with no skipped/unresolved items does not print those sections."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    _patch_pipeline(monkeypatch, tmp_path, set())
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Skipped items:" not in out
    assert "Unresolved items:" not in out
    assert "Failed items:" not in out


def test_playlist_skipped_all_skipped(monkeypatch, tmp_path, capsys):
    """A playlist with only skipped items reports them all."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    _patch_pipeline_skips(monkeypatch, tmp_path, {"v1", "v2"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Skipped    : 2" in out
    assert "Downloaded : 0" in out
    assert out.split("Skipped items:")[1].count("- ") == 2


def test_playlist_step72_failed_report_still_works(monkeypatch, tmp_path, capsys):
    """Step 7.2 failed-item reporting remains intact alongside new sections."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    _patch_pipeline(monkeypatch, tmp_path, {"v2"})
    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Failed     : 1" in out
    assert "Failed items:" in out
    assert "  ✗ Beta" in out
    assert "Reason: boom" in out


def test_playlist_report_single_video_unchanged(monkeypatch, tmp_path, capsys):
    """Standalone single-video download does not print any report sections."""
    info = {"id": "v1", "title": "One", "formats": [{"format_id": "0", "height": 480}]}
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: o / "x.mp4")
    cli._download_video(info)
    out = capsys.readouterr().out
    assert "Skipped items:" not in out
    assert "Unresolved items:" not in out
    assert "Failed items:" not in out
    assert "✓ Download completed" in out


def test_playlist_report_lazy_safe(monkeypatch, tmp_path, capsys):
    """Lazy playlist processing remains intact with the new report sections."""
    produced = []

    def gen():
        for i in range(3):
            produced.append(i)
            yield _resolved(f"v{i}", f"Title {i}")

    info = {"_type": "playlist", "title": "Lazy", "entries": gen()}
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i_, s, o: o)
    stats = cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    assert produced == [0, 1, 2]
    assert stats["total"] == 3
    assert stats["downloaded"] == 3
    assert "Skipped items:" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Phase 7 / Step 7.4: Playlist resume / retry UX
# ---------------------------------------------------------------------------

def _retry_item(index, entry, resolved=None, qualities=None):
    return {"index": index, "entry": entry, "resolved": resolved, "qualities": qualities}


def _retry_resolved_items(entries):
    return [_retry_item(i, e, resolved=e, qualities={480: _selected()}) for i, e in enumerate(entries)]


def _all_retry_prompts(prompts):
    return [p for p in prompts if "Retry failed/unresolved items?" in p]


def test_retry_no_prompt_when_everything_succeeds(monkeypatch, tmp_path, capsys):
    """Test 1: with no failed/unresolved items no retry prompt is shown."""
    entries = [_resolved("v1", "One"), _resolved("v2", "Two"), _resolved("v3", "Three")]
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda *a, **k: calls.append(a[0]["id"]) or "downloaded")
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail("retry prompt must not appear"))

    cli._run_with_retries(_retry_resolved_items(entries), chosen_height=480, playlist_dir=tmp_path)
    assert calls == ["v1", "v2", "v3"]
    out = capsys.readouterr().out
    assert "Playlist complete" in out
    assert "Retry" not in out
    assert "Retried" not in out


def test_retry_prompt_appears_for_failed_and_decline_exits(monkeypatch, tmp_path, capsys):
    """Test 2: a failed item shows the retry prompt; answering 'n' skips retry."""
    entries = [_resolved("v1", "One")]
    attempts = []
    monkeypatch.setattr(cli, "_download_video", lambda *a, **k: attempts.append(a[0]["id"]) or "failed")
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "n")

    cli._run_with_retries(_retry_resolved_items(entries), chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert attempts == ["v1"]
    assert len(_all_retry_prompts(prompts)) == 1
    assert "Retried" not in out


def test_retry_only_failed_items(monkeypatch, tmp_path, capsys):
    """Test 3: only the failed item is retried; downloaded/skipped are untouched."""
    entries = [_resolved("v1", "One"), _resolved("v2", "Two"), _resolved("v3", "Three")]
    counts = {}

    def fake_download(resolved, **k):
        vid = resolved["id"]
        n = counts.get(vid, 0) + 1
        counts[vid] = n
        if vid == "v3":
            return "failed" if n == 1 else "downloaded"
        return "skipped" if vid == "v2" else "downloaded"

    monkeypatch.setattr(cli, "_download_video", fake_download)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "y")

    cli._run_with_retries(_retry_resolved_items(entries), chosen_height=480, playlist_dir=tmp_path)
    assert counts["v1"] == 1
    assert counts["v2"] == 1
    assert counts["v3"] == 2
    assert len(_all_retry_prompts(prompts)) == 1
    out = capsys.readouterr().out
    assert "Retried     : 1" in out
    assert "Downloaded  : 1" in out


def test_retry_only_unresolved_items(monkeypatch, tmp_path, capsys):
    """Test 4: an unresolved item is re-resolved and retried; success items untouched."""
    entries = [_resolved("v1", "One"), _resolved("u1", "Two")]
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    resolve_state = {"u1": 0}

    def fake_resolve(entry):
        if entry["id"] == "u1":
            resolve_state["u1"] += 1
            return None if resolve_state["u1"] == 1 else entry
        return entry

    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    download_counts = {}

    def fake_download(resolved, **k):
        vid = resolved["id"]
        download_counts[vid] = download_counts.get(vid, 0) + 1
        return "downloaded"

    monkeypatch.setattr(cli, "_download_video", fake_download)
    items = [
        _retry_item(0, entries[0], resolved=entries[0], qualities={480: _selected()}),
        _retry_item(1, entries[1], resolved=None, qualities=None),
    ]
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "y")

    cli._run_with_retries(items, chosen_height=480, playlist_dir=tmp_path)
    assert download_counts["v1"] == 1
    assert download_counts["u1"] == 1
    assert resolve_state["u1"] == 2
    out = capsys.readouterr().out
    assert "Retried     : 1" in out
    assert "Downloaded  : 1" in out


def test_retry_mixed_failed_and_unresolved(monkeypatch, tmp_path, capsys):
    """Test 5: mixed failed+unresolved retries exactly the pending items in order."""
    entries = [
        _resolved("dl", "DL"),
        _resolved("fa", "FA"),
        _resolved("fb", "FB"),
        _resolved("dl2", "DL2"),
        _resolved("ua", "UA"),
    ]
    attempts = []

    def fake_download(*a, **k):
        vid = a[0]["id"]
        attempts.append(vid)
        return "downloaded" if vid.startswith("dl") else "failed"

    monkeypatch.setattr(cli, "_download_video", fake_download)
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: attempts.append(e["id"]) or None)
    items = [
        _retry_item(0, entries[0], resolved=entries[0], qualities={480: _selected()}),
        _retry_item(1, entries[1], resolved=entries[1], qualities={480: _selected()}),
        _retry_item(2, entries[2], resolved=entries[2], qualities={480: _selected()}),
        _retry_item(3, entries[3], resolved=entries[3], qualities={480: _selected()}),
        _retry_item(4, entries[4], resolved=None, qualities=None),
    ]
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda p: next(answers))

    cli._run_with_retries(items, chosen_height=480, playlist_dir=tmp_path)
    # Initial pass downloads dl & dl2, fails fa & fb, leaves ua unresolved.
    # Retry batch is the two failed (fa, fb) then the unresolved (ua) in order.
    assert attempts[-3:] == ["fa", "fb", "ua"]


def test_retry_success_clears_pending(monkeypatch, tmp_path, capsys):
    """Test 6: a failed item that succeeds on retry is reported downloaded and no longer pending."""
    entries = [_resolved("v1", "One")]
    n = [0]

    def fake_download(*a, **k):
        n[0] += 1
        return "failed" if n[0] == 1 else "downloaded"

    monkeypatch.setattr(cli, "_download_video", fake_download)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "y")

    cli._run_with_retries(_retry_resolved_items(entries), chosen_height=480, playlist_dir=tmp_path)
    assert n[0] == 2
    assert len(_all_retry_prompts(prompts)) == 1
    out = capsys.readouterr().out
    assert "Retried     : 1" in out
    assert "Downloaded  : 1" in out
    assert "Failed      : 0" in out


def test_retry_remains_failed_offers_again(monkeypatch, tmp_path, capsys):
    """Test 7: an item that keeps failing is offered another retry."""
    entries = [_resolved("v1", "One")]
    n = [0]
    monkeypatch.setattr(cli, "_download_video", lambda *a, **k: n.__setitem__(0, n[0] + 1) or "failed")
    prompts = []
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or next(answers))

    cli._run_with_retries(_retry_resolved_items(entries), chosen_height=480, playlist_dir=tmp_path)
    assert n[0] == 2
    assert len(_all_retry_prompts(prompts)) == 2


def test_retry_remains_unresolved_no_crash(monkeypatch, tmp_path, capsys):
    """Test 8: an unresolved item that cannot resolve again stays unresolved without crashing."""
    entries = [_resolved("u1", "Two")]
    resolve_calls = [0]
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: resolve_calls.__setitem__(0, resolve_calls[0] + 1) or None)
    prompts = []
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or next(answers))

    cli._run_with_retries(
        [_retry_item(0, entries[0], resolved=None, qualities=None)], chosen_height=480, playlist_dir=tmp_path
    )
    assert resolve_calls[0] == 2
    assert len(_all_retry_prompts(prompts)) == 2
    out = capsys.readouterr().out
    assert "Unresolved : 1" in out


def test_retry_quality_menu_once_and_preserved(monkeypatch, tmp_path, data_dir, capsys):
    """Test 9: the quality menu appears only once and retry reuses the chosen quality."""
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One")])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    quality_calls = []
    monkeypatch.setattr(cli, "select_quality", lambda q: quality_calls.append(1) or q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    state = {"v1": 0}

    def fake_download(info_, sel, out):
        state["v1"] += 1
        if state["v1"] == 1:
            raise DownloadFailure("boom")
        target = out / f"{info_['id']}.mp4"
        target.write_bytes(b"d")
        return target

    monkeypatch.setattr(cli, "download_media", fake_download)
    answers = iter(["http://x", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda p: next(answers))
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert len(quality_calls) == 1
    assert out.count("Playlist quality") == 1
    assert "Retry complete" in out
    assert "Retried     : 1" in out


def test_retry_uses_same_output_directory(monkeypatch, tmp_path, data_dir, capsys):
    """Test 10: retry writes into the same playlist directory."""
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One")])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    seen_dirs = []
    state = {"v1": 0}

    def fake_download(info_, sel, out):
        seen_dirs.append(str(out))
        state["v1"] += 1
        if state["v1"] == 1:
            raise DownloadFailure("boom")
        target = out / f"{info_['id']}.mp4"
        target.write_bytes(b"d")
        return target

    monkeypatch.setattr(cli, "download_media", fake_download)
    answers = iter(["http://x", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda p: next(answers))
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    pdir = tmp_path / "My_Playlist"
    assert len(seen_dirs) == 2
    assert seen_dirs[0] == str(pdir) and seen_dirs[1] == str(pdir)
    assert (pdir / "v1.mp4").exists()
    assert not (tmp_path / "v1.mp4").exists()


def test_retry_order_preserved_for_non_adjacent_pending(monkeypatch, tmp_path, capsys):
    """Test 11: non-adjacent failed/unresolved items retry in original playlist order."""
    entries = [
        _resolved("fa", "FA"),
        _resolved("ok1", "OK1"),
        _resolved("ua", "UA"),
        _resolved("ok2", "OK2"),
        _resolved("fb", "FB"),
    ]
    attempts = []

    def fake_download(*a, **k):
        vid = a[0]["id"]
        attempts.append(vid)
        return "downloaded" if vid.startswith("ok") else "failed"

    monkeypatch.setattr(cli, "_download_video", fake_download)
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: attempts.append(e["id"]) or None)
    # Playlist order: fa(fail), ok1(download), ua(unresolved), ok2(download), fb(fail)
    # Pending items are fa (index 0), ua (index 2), fb (index 4) -> must retry fa, ua, fb
    items = [
        _retry_item(0, entries[0], resolved=entries[0], qualities={480: _selected()}),
        _retry_item(1, entries[1], resolved=entries[1], qualities={480: _selected()}),
        _retry_item(2, entries[2], resolved=None, qualities=None),  # unresolved
        _retry_item(3, entries[3], resolved=entries[3], qualities={480: _selected()}),
        _retry_item(4, entries[4], resolved=entries[4], qualities={480: _selected()}),
    ]
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda p: next(answers))

    cli._run_with_retries(items, chosen_height=480, playlist_dir=tmp_path)
    # Initial pass downloads ok1 & ok2, fails fa & fb, unresolved ua.
    # Retry batch (in original playlist order) = fa, ua, fb.
    assert attempts[-3:] == ["fa", "ua", "fb"]


def test_retry_declined_no_processing(monkeypatch, tmp_path, capsys):
    """Test 12: declining the retry runs no further processing and exits normally."""
    entries = [_resolved("v1", "One")]
    attempts = []
    monkeypatch.setattr(cli, "_download_video", lambda *a, **k: attempts.append(a[0]["id"]) or "failed")
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "n")

    cli._run_with_retries(_retry_resolved_items(entries), chosen_height=480, playlist_dir=tmp_path)
    assert attempts == ["v1"]
    assert len(_all_retry_prompts(prompts)) == 1
    out = capsys.readouterr().out
    assert "Playlist complete" in out
    assert "Retried" not in out


def test_retry_multiple_rounds_two_explicit_confirmations(monkeypatch, tmp_path, capsys):
    """Test 13: multiple retry rounds each require explicit confirmation; no auto-retry."""
    entries = [_resolved("fA", "FA"), _resolved("fB", "FB"), _resolved("uA", "UA")]
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    counts = {}

    def fake_download(*a, **k):
        vid = a[0]["id"]
        n = counts.get(vid, 0) + 1
        counts[vid] = n
        if vid == "fA":
            return "failed" if n < 3 else "downloaded"
        if vid == "fB":
            return "failed" if n < 2 else "downloaded"
        return "downloaded"

    monkeypatch.setattr(cli, "_download_video", fake_download)
    resolve_state = {"uA": 0}

    def fake_resolve(entry):
        resolve_state[entry["id"]] += 1
        # uA resolves on its 3rd attempt (initial + retry1 + retry2)
        return None if resolve_state[entry["id"]] < 3 else entry

    monkeypatch.setattr(cli, "_resolve_playlist_entry", fake_resolve)
    items = [
        _retry_item(0, entries[0], resolved=entries[0], qualities={480: _selected()}),
        _retry_item(1, entries[1], resolved=entries[1], qualities={480: _selected()}),
        _retry_item(2, entries[2], resolved=None, qualities=None),
    ]
    prompts = []
    answers = iter(["y", "y", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or next(answers))

    cli._run_with_retries(items, chosen_height=480, playlist_dir=tmp_path)
    retry_prompts = _all_retry_prompts(prompts)
    # 1 after initial run, 1 after retry round one -> two explicit confirmations.
    assert len(retry_prompts) == 2
    # Round progression: initial: fA fail, fB fail, uA unresolved.
    # Retry1: fA fail(2), fB ok(2), uA unresolved(2). Retry2: fA ok(3), uA ok(3).
    assert counts["fA"] == 3
    assert counts["fB"] == 2
    assert resolve_state["uA"] == 3
    out = capsys.readouterr().out
    assert out.count("Retry complete") == 2


def test_retry_reporting_and_step72_73_regression(monkeypatch, tmp_path, capsys):
    """Test 14: retry summary plus the existing failed/skipped/unresolved reports coexist."""
    entries = [
        _resolved("dl", "DL"),
        _resolved("sk", "SK"),
        _resolved("fl", "FL"),
        _resolved("ur", "UR"),
    ]
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})

    def fake_download(*a, **k):
        vid = a[0]["id"]
        return "failed" if vid == "fl" else ("skipped" if vid == "sk" else "downloaded")

    monkeypatch.setattr(cli, "_download_video", fake_download)
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: None)
    items = [
        _retry_item(0, entries[0], resolved=entries[0], qualities={480: _selected()}),
        _retry_item(1, entries[1], resolved=entries[1], qualities={480: _selected()}),
        _retry_item(2, entries[2], resolved=entries[2], qualities={480: _selected()}),
        _retry_item(3, entries[3], resolved=None, qualities=None),
    ]
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "n")

    cli._run_with_retries(items, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Downloaded : 1" in out
    assert "Skipped    : 1" in out
    assert "Failed     : 1" in out
    assert "Unresolved : 1" in out
    assert "Skipped items:" in out and "Reason: Already downloaded." in out
    assert "Unresolved items:" in out and "Reason: Could not resolve video information." in out
    assert "Failed items:" in out and "  ✗ FL" in out


# ---------------------------------------------------------------------------
# Phase 7 / Step 7.5: Final playlist UX polish
# ---------------------------------------------------------------------------

def test_ux_successful_playlist_output_clean(monkeypatch, tmp_path, capsys):
    """A clean run prints no retry/scope markers and no empty report sections."""
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    calls = []
    monkeypatch.setattr(cli, "_download_video", lambda *a, **k: calls.append(a[0]["id"]) or "downloaded")
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail("clean run must not prompt for retry"))

    cli._process_playlist(info)
    out = capsys.readouterr().out
    assert calls == ["v1", "v2"]
    assert "Playlist complete" in out
    assert "Total      : 2" in out
    assert "Downloaded : 2" in out
    # No empty report sections on a fully successful playlist.
    assert "Skipped items:" not in out
    assert "Failed items:" not in out
    assert "Unresolved items:" not in out
    # No retry-related markers.
    assert "Retrying failed/unresolved items..." not in out
    assert "Retry round totals" not in out
    assert "Retry complete" not in out


def test_ux_skipped_report_format_still_correct(monkeypatch, tmp_path, capsys):
    """The skipped report keeps its marker, title and reason."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: {"dup": True} if i["id"] == "v2" else None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: o / f"{i['id']}.mp4")

    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Skipped items:" in out
    assert "  - Beta" in out
    assert "    Reason: Already downloaded." in out
    assert "Failed items:" not in out
    assert "Unresolved items:" not in out


def test_ux_failed_report_format_still_correct(monkeypatch, tmp_path, capsys):
    """The failed report keeps its marker, title and reason."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _resolved("v2", "Beta")])
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def flaky(local_info, sel, out):
        if local_info["id"] == "v2":
            raise DownloadFailure("boom")
        return out / f"{local_info['id']}.mp4"

    monkeypatch.setattr(cli, "download_media", flaky)

    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Failed items:" in out
    assert "  ✗ Beta" in out
    assert "    Reason: boom" in out
    assert "Skipped items:" not in out
    assert "Unresolved items:" not in out


def test_ux_unresolved_report_format_still_correct(monkeypatch, tmp_path, capsys):
    """The unresolved report keeps its marker, title and reason."""
    info = _playlist_info(entries=[_resolved("v1", "Alpha"), _partial("u1", "Beta")])
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: o / f"{i['id']}.mp4")
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: None if e.get("id") == "u1" else e)

    cli._process_playlist(info, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Unresolved items:" in out
    assert "  ? Beta" in out
    assert "    Reason: Could not resolve video information." in out
    assert "Failed items:" not in out
    assert "Skipped items:" not in out


def test_ux_retry_heading_distinguishes_retry_pass(monkeypatch, tmp_path, capsys):
    """The retry pass is introduced with an explicit heading before retry progress."""
    entries = [_resolved("v1", "One"), _resolved("v2", "Two")]
    n = [0]

    def fake_download(*a, **k):
        n[0] += 1
        return "failed"     # always fails, forcing a visible retry pass

    monkeypatch.setattr(cli, "_download_video", fake_download)
    prompts = []
    answers = iter(["y", "n"])  # accept retry, then decline the follow-up
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or next(answers))

    cli._run_with_retries(_retry_resolved_items(entries), chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Retry item 1/2" in out
    # The heading marking the transition from initial to retry processing is present.
    assert "Retrying failed/unresolved items..." in out
    assert out.index("Retrying failed/unresolved items...") < out.index("Retry item 1/2")
    # The heading never appears during the initial pass.
    assert "Retry complete" in out


def test_ux_retry_summary_clarifies_scope(monkeypatch, tmp_path, capsys):
    """The retry summary states its numbers describe only the current retry round."""
    entries = [_resolved("v1", "One")]
    n = [0]

    def fake_download(*a, **k):
        n[0] += 1
        return "failed" if n[0] == 1 else "downloaded"

    monkeypatch.setattr(cli, "_download_video", fake_download)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "y")

    cli._run_with_retries(_retry_resolved_items(entries), chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Retry complete" in out
    # A subheading makes the scope explicit so counts are not read as playlist totals.
    assert "Retry round totals" in out
    assert "Retried     : 1" in out
    assert "Downloaded  : 1" in out


def test_ux_quality_menu_shown_only_once_during_retry(monkeypatch, tmp_path, data_dir, capsys):
    """Step 7.4 invariant: the quality menu appears once, never again on retry."""
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    info = _playlist_info(title="My Playlist", entries=[_resolved("v1", "One"), _resolved("v2", "Two")])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    quality_calls = []
    monkeypatch.setattr(cli, "select_quality", lambda q: quality_calls.append(1) or q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    state = {"v2": 0}

    def fake_download(local_info, sel, out):
        if local_info["id"] == "v2":
            state["v2"] += 1
            if state["v2"] == 1:
                raise DownloadFailure("boom")
        target = out / f"{local_info['id']}.mp4"
        target.write_bytes(b"d")
        return target

    monkeypatch.setattr(cli, "download_media", fake_download)
    answers = iter(["http://x", "y", "y"])  # url, confirm, accept retry
    monkeypatch.setattr("builtins.input", lambda p: next(answers))
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert len(quality_calls) == 1
    assert out.count("Playlist quality") == 1


def test_ux_retry_only_failed_unresolved_in_order(monkeypatch, tmp_path, capsys):
    """Step 7.4 invariants: retry touches only failed/unresolved items, original order."""
    entries = [
        _resolved("dl", "DL"),
        _resolved("fa", "FA"),
        _resolved("ua", "UA"),
        _resolved("dl2", "DL2"),
        _resolved("fb", "FB"),
    ]
    attempts = []

    def fake_download(*a, **k):
        vid = a[0]["id"]
        attempts.append(vid)
        return "downloaded" if vid.startswith("dl") else "failed"

    monkeypatch.setattr(cli, "_download_video", fake_download)
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: attempts.append(e["id"]) or None)
    items = [
        _retry_item(0, entries[0], resolved=entries[0], qualities={480: _selected()}),
        _retry_item(1, entries[1], resolved=entries[1], qualities={480: _selected()}),
        _retry_item(2, entries[2], resolved=None, qualities=None),  # unresolved
        _retry_item(3, entries[3], resolved=entries[3], qualities={480: _selected()}),
        _retry_item(4, entries[4], resolved=entries[4], qualities={480: _selected()}),
    ]
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda p: next(answers))

    cli._run_with_retries(items, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    # Only pending (failed + unresolved) are retried, in original order: fa, ua, fb.
    assert attempts[-3:] == ["fa", "ua", "fb"]
    assert "Retry item 1/3" in out
    assert "Retry item 2/3" in out
    assert "Retry item 3/3" in out


def test_ux_reports_only_printed_when_nonempty(monkeypatch, tmp_path, capsys):
    """Empty report categories are skipped; only the non-empty one is printed."""
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def flaky(local_info, sel, out):
        if local_info["id"] == "v2":
            raise DownloadFailure("boom")
        return out / f"{local_info['id']}.mp4"

    monkeypatch.setattr(cli, "download_media", flaky)

    cli._process_playlist(
        _playlist_info(entries=[_resolved("v1", "One"), _resolved("v2", "Two")]),
        chosen_height=480, playlist_dir=tmp_path,
    )
    out = capsys.readouterr().out
    # Only the failed section appears; skipped/unresolved are empty and unprinted.
    assert "Failed items:" in out
    assert "Skipped items:" not in out
    assert "Unresolved items:" not in out
    assert "Downloaded : 1" in out
    assert "Failed     : 1" in out
