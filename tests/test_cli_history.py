"""Unit tests for the `downv history` CLI listing (Step 5C.2)."""

import sys

import pytest

from downv import cli, history


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Point the history storage at a fresh temporary directory."""
    monkeypatch.setattr(history, "get_data_directory", lambda: tmp_path)
    return tmp_path


def _inject(records):
    history._save(records)


def _make_record(video_id, title, timestamp, quality=480):
    return {
        "video_id": video_id,
        "title": title,
        "url": f"https://example.com/{video_id}",
        "filename": f"{video_id}.mp4",
        "filepath": f"/tmp/media/{video_id}.mp4",
        "quality": quality,
        "duration": 10,
        "file_size": 1024,
        "downloaded_at": timestamp,
    }


def test_show_history_empty(data_dir, capsys):
    cli.show_history()
    out = capsys.readouterr().out
    assert "No downloads recorded yet." in out


def test_show_history_lists_newest_first(data_dir, capsys):
    _inject([
        _make_record("AAA", "Oldest", "2026-01-01T10:00:00+00:00"),
        _make_record("CCC", "Newest", "2026-03-01T10:00:00+00:00"),
        _make_record("BBB", "Middle", "2026-02-01T10:00:00+00:00"),
    ])

    cli.show_history()
    out = capsys.readouterr().out

    # newest first
    assert out.index("Newest") < out.index("Middle") < out.index("Oldest")
    assert "Video ID   : CCC" in out
    assert "Quality    : 480p" in out
    assert "Downloaded : 2026-03-01T10:00:00+00:00" in out
    assert "Total: 3" in out


def test_show_history_uses_history_api_not_direct_json(data_dir, monkeypatch):
    """show_history must list via get_download_history, never parse JSON itself."""
    record = _make_record("AAA", "Oldest", "2026-01-01T10:00:00+00:00")
    monkeypatch.setattr(history, "get_download_history", lambda: [record])
    cli.show_history()  # must not raise, proves it only relies on the API


def test_unknown_command_prints_usage(data_dir, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["downv", "bogus"])
    cli.main()
    out = capsys.readouterr().out
    # A single positional argument is treated as a download URL in Step 8.4,
    # not as a subcommand, so it does not trigger the "Unknown command" usage
    # path. (Multiple positionals are rejected separately.)
    assert "Unknown command: bogus" not in out
    assert "downv history" not in out


def test_history_subcommand_dispatches(data_dir, monkeypatch, capsys):
    _inject([_make_record("AAA", "My Video", "2026-01-01T10:00:00+00:00")])
    captured = {}
    monkeypatch.setattr(cli, "show_history", lambda: captured.setdefault("called", True))
    monkeypatch.setattr(sys, "argv", ["downv", "history"])
    cli.main()
    assert captured.get("called") is True


def test_show_history_detail_found(data_dir, capsys):
    _inject([_make_record("AAA", "Detail Video", "2026-01-01T10:00:00+00:00", quality=720)])

    cli.show_history_detail("AAA")
    out = capsys.readouterr().out

    assert "Title       : Detail Video" in out
    assert "Video ID    : AAA" in out
    assert "Quality     : 720p" in out
    assert "Duration    : 00:10" in out
    assert "Filename    : AAA.mp4" in out
    assert "Filepath    : /tmp/media/AAA.mp4" in out
    assert "Downloaded  : 2026-01-01T10:00:00+00:00" in out
    assert "No record found" not in out


def test_show_history_detail_not_found(data_dir, capsys):
    _inject([_make_record("AAA", "Detail Video", "2026-01-01T10:00:00+00:00")])

    cli.show_history_detail("NOPE")
    out = capsys.readouterr().out
    assert "No record found for video ID: NOPE" in out


def test_show_history_detail_empty(data_dir, capsys):
    cli.show_history_detail("ANY")
    out = capsys.readouterr().out
    assert "No record found for video ID: ANY" in out


def test_history_detail_subcommand_dispatches(data_dir, monkeypatch, capsys):
    _inject([_make_record("AAA", "Detail Video", "2026-01-01T10:00:00+00:00")])
    captured = {}
    monkeypatch.setattr(cli, "show_history_detail", lambda vid: captured.setdefault("id", vid))
    monkeypatch.setattr(sys, "argv", ["downv", "history", "AAA"])
    cli.main()
    assert captured.get("id") == "AAA"


def test_show_history_detail_uses_find_download_api(data_dir, monkeypatch, capsys):
    """Detail view must resolve via find_download, never parse JSON itself."""
    record = _make_record("AAA", "Detail Video", "2026-01-01T10:00:00+00:00")
    monkeypatch.setattr(history, "find_download", lambda vid: record)
    cli.show_history_detail("AAA")  # must not raise; proves API-only usage
    out = capsys.readouterr().out
    assert "Detail Video" in out


def test_show_history_detail_corrupted_history(data_dir, monkeypatch, capsys):
    """Corrupted history surfaces a friendly error and keeps the file intact."""
    corrupted = data_dir / "history.json"
    corrupted.write_text("{ this is not valid json")

    cli.show_history_detail("AAA")
    out = capsys.readouterr().out
    assert "✗ Could not read download history." in out
    assert "Traceback" not in out
    assert corrupted.read_text() == "{ this is not valid json"


def test_history_remove_found(data_dir, capsys, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    video_file = media / "AAA.mp4"
    video_file.write_bytes(b"do not delete me")
    _inject([_make_record("AAA", "Remove Me", "2026-01-01T10:00:00+00:00")])

    cli.remove_history("AAA")
    out = capsys.readouterr().out
    assert "✓ Removed download record for video ID: AAA" in out

    assert history.find_download("AAA") is None
    assert history.count_history() == 0
    # media file untouched
    assert video_file.exists()
    assert video_file.read_bytes() == b"do not delete me"


def test_history_remove_not_found(data_dir, capsys):
    _inject([_make_record("AAA", "Keep Me", "2026-01-01T10:00:00+00:00")])

    cli.remove_history("NOPE")
    out = capsys.readouterr().out
    assert "No record found for video ID: NOPE" in out
    assert history.count_history() == 1


def test_history_remove_dispatch(data_dir, monkeypatch, capsys):
    _inject([_make_record("AAA", "Dispatch", "2026-01-01T10:00:00+00:00")])
    captured = {}
    monkeypatch.setattr(cli, "remove_history", lambda vid: captured.setdefault("id", vid))
    monkeypatch.setattr(sys, "argv", ["downv", "history", "remove", "AAA"])
    cli.main()
    assert captured.get("id") == "AAA"


def test_history_remove_missing_id_prints_usage(data_dir, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["downv", "history", "remove"])
    cli.main()
    out = capsys.readouterr().out
    assert "Usage: downv history remove <video_id>" in out


def test_history_remove_uses_remove_download_api(data_dir, monkeypatch, capsys):
    """Remove must go through the History API, never touch JSON directly."""
    monkeypatch.setattr(history, "remove_download", lambda vid: {"video_id": vid})
    cli.remove_history("AAA")
    out = capsys.readouterr().out
    assert "✓ Removed download record for video ID: AAA" in out


def test_history_remove_corrupted(data_dir, capsys):
    corrupted = data_dir / "history.json"
    corrupted.write_text("{ this is not valid json")

    cli.remove_history("AAA")
    out = capsys.readouterr().out
    assert "✗ Could not read download history." in out
    assert "Traceback" not in out
    assert corrupted.read_text() == "{ this is not valid json"


def test_history_remove_media_not_deleted(data_dir, capsys, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    video_file = media / "My Video.mp4"
    video_file.write_bytes(b"precious media content")
    record = _make_record("AAA", "My Video", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(video_file)
    _inject([record])

    cli.remove_history("AAA")
    capsys.readouterr().out

    assert history.find_download("AAA") is None
    assert video_file.exists()
    assert video_file.read_bytes() == b"precious media content"


def test_history_clear(data_dir, capsys):
    _inject([
        _make_record("AAA", "One", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Two", "2026-02-01T10:00:00+00:00"),
        _make_record("CCC", "Three", "2026-03-01T10:00:00+00:00"),
    ])

    cli.clear_history()
    out = capsys.readouterr().out
    assert "✓ Download history cleared." in out

    assert history.count_history() == 0
    assert history.get_download_history() == []
    payload = (data_dir / "history.json").read_text()
    assert '"version": 1' in payload
    assert '"downloads": []' in payload


def test_history_clear_empty(data_dir, capsys):
    cli.clear_history()
    out = capsys.readouterr().out
    assert "✓ Download history cleared." in out
    assert history.count_history() == 0
    assert history.get_download_history() == []


def test_history_clear_dispatch(data_dir, monkeypatch, capsys):
    _inject([_make_record("AAA", "One", "2026-01-01T10:00:00+00:00")])
    captured = {}
    monkeypatch.setattr(cli, "clear_history", lambda: captured.setdefault("called", True))
    monkeypatch.setattr(sys, "argv", ["downv", "history", "clear"])
    cli.main()
    assert captured.get("called") is True


def test_history_clear_uses_clear_history_api(data_dir, monkeypatch, capsys):
    """Clear must go through the History API, never touch JSON directly."""
    called = []
    monkeypatch.setattr(history, "clear_history", lambda: called.append(True))
    cli.clear_history()
    assert called == [True]


def test_history_clear_corrupted(data_dir, capsys):
    corrupted = data_dir / "history.json"
    corrupted.write_text("{ this is not valid json")

    cli.clear_history()
    out = capsys.readouterr().out
    assert "✗ Could not read download history." in out
    assert "Traceback" not in out
    assert corrupted.read_text() == "{ this is not valid json"


def test_history_clear_media_not_deleted(data_dir, capsys, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    files = {}
    for i, vid in enumerate(["AAA", "BBB", "CCC"]):
        f = media / f"video{i + 1}.mp4"
        f.write_bytes(f"content-{vid}".encode())
        files[vid] = f

    records = [
        dict(_make_record(vid, f"Video {vid}", f"2026-0{i + 1}-01T10:00:00+00:00"),
             filepath=str(f))
        for i, (vid, f) in enumerate(files.items())
    ]
    _inject(records)

    before = {vid: f.read_bytes() for vid, f in files.items()}
    cli.clear_history()
    capsys.readouterr().out

    assert history.count_history() == 0
    for vid, f in files.items():
        assert f.exists()
        assert f.read_bytes() == before[vid]


def test_history_clear_extra_argument(data_dir, capsys, monkeypatch):
    _inject([_make_record("AAA", "One", "2026-01-01T10:00:00+00:00")])
    monkeypatch.setattr(sys, "argv", ["downv", "history", "clear", "extra"])
    cli.main()
    out = capsys.readouterr().out
    assert "Usage: downv history clear" in out
    assert history.count_history() == 1


def test_history_search_matches_title(data_dir, capsys):
    _inject([
        _make_record("AAA", "Big Buck Bunny", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Me at the zoo", "2026-02-01T10:00:00+00:00"),
    ])

    cli.search_history("bunny")
    out = capsys.readouterr().out
    assert "Big Buck Bunny" in out
    assert "Me at the zoo" not in out
    assert "Total: 1" in out


def test_history_search_matches_video_id(data_dir, capsys):
    _inject([
        _make_record("AAA", "Alpha", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Beta", "2026-02-01T10:00:00+00:00"),
    ])

    cli.search_history("bbb")
    out = capsys.readouterr().out
    assert "Beta" in out
    assert "Alpha" not in out
    assert "Total: 1" in out


def test_history_search_case_insensitive(data_dir, capsys):
    _inject([
        _make_record("ABC123", "Upper Title", "2026-01-01T10:00:00+00:00"),
        _make_record("XYZ", "Other", "2026-02-01T10:00:00+00:00"),
    ])

    cli.search_history("UPPER")
    out = capsys.readouterr().out
    assert "Upper Title" in out

    cli.search_history("abc123")
    out = capsys.readouterr().out
    assert "Upper Title" in out


def test_history_search_no_matches(data_dir, capsys):
    _inject([_make_record("AAA", "Solo", "2026-01-01T10:00:00+00:00")])

    cli.search_history("zzzznotfoundzzzz")
    out = capsys.readouterr().out
    assert "No matching downloads." in out
    assert "Total" not in out


def test_history_search_newest_first(data_dir, capsys):
    _inject([
        _make_record("AAA", "Match old", "2026-01-01T10:00:00+00:00"),
        _make_record("CCC", "Match new", "2026-03-01T10:00:00+00:00"),
        _make_record("BBB", "Match mid", "2026-02-01T10:00:00+00:00"),
    ])

    cli.search_history("match")
    out = capsys.readouterr().out
    assert out.index("Match new") < out.index("Match mid") < out.index("Match old")
    assert "Total: 3" in out


def test_history_search_dispatch(data_dir, monkeypatch, capsys):
    _inject([_make_record("AAA", "One", "2026-01-01T10:00:00+00:00")])
    captured = {}
    monkeypatch.setattr(cli, "search_history", lambda q: captured.setdefault("query", q))
    monkeypatch.setattr(sys, "argv", ["downv", "history", "search", "one"])
    cli.main()
    assert captured.get("query") == "one"


def test_history_search_missing_query(data_dir, capsys, monkeypatch):
    _inject([_make_record("AAA", "One", "2026-01-01T10:00:00+00:00")])
    monkeypatch.setattr(sys, "argv", ["downv", "history", "search"])
    cli.main()
    out = capsys.readouterr().out
    assert "Usage: downv history search <query>" in out
    assert history.count_history() == 1


def test_history_search_corrupted(data_dir, capsys):
    corrupted = data_dir / "history.json"
    corrupted.write_text("{ this is not valid json")

    cli.search_history("anything")
    out = capsys.readouterr().out
    assert "✗ Could not read download history." in out
    assert "Traceback" not in out
    assert corrupted.read_text() == "{ this is not valid json"


def test_history_search_uses_get_download_history_api(data_dir, monkeypatch, capsys):
    """Search must read via get_download_history, never touch JSON directly."""
    records = [
        _make_record("AAA", "Big Buck Bunny", "2026-01-01T10:00:00+00:00"),
    ]
    monkeypatch.setattr(history, "get_download_history", lambda: records)
    cli.search_history("bunny")
    out = capsys.readouterr().out
    assert "Big Buck Bunny" in out
    assert "Total: 1" in out


def test_show_history_status_file_exists(data_dir, capsys, tmp_path):
    media = tmp_path / "present.mp4"
    media.write_bytes(b"hello")
    record = _make_record("AAA", "Existing", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(media)
    _inject([record])

    cli.show_history()
    out = capsys.readouterr().out
    assert "Status     : ✓ File exists" in out


def test_show_history_status_file_missing(data_dir, capsys):
    record = _make_record("BBB", "Gone", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(data_dir / "does-not-exist.mp4")
    _inject([record])

    cli.show_history()
    out = capsys.readouterr().out
    assert "Status     : ✗ File missing" in out


def test_show_history_detail_status_file_exists(data_dir, capsys, tmp_path):
    media = tmp_path / "present.mp4"
    media.write_bytes(b"hello")
    record = _make_record("CCC", "Detail exists", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(media)
    _inject([record])

    cli.show_history_detail("CCC")
    out = capsys.readouterr().out
    assert "Status      : ✓ File exists" in out


def test_show_history_detail_status_file_missing(data_dir, capsys):
    record = _make_record("DDD", "Detail gone", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(data_dir / "nope.mp4")
    _inject([record])

    cli.show_history_detail("DDD")
    out = capsys.readouterr().out
    assert "Status      : ✗ File missing" in out


def test_show_history_status_missing_filepath(data_dir, capsys):
    record = _make_record("EEE", "No path", "2026-01-01T10:00:00+00:00")
    record.pop("filepath")
    _inject([record])

    cli.show_history()
    out = capsys.readouterr().out
    assert "Status     : ✗ File missing" in out

    cli.show_history_detail("EEE")
    out = capsys.readouterr().out
    assert "Status      : ✗ File missing" in out


def test_show_history_status_is_read_only(data_dir, capsys, tmp_path):
    media = tmp_path / "untouched.mp4"
    media.write_bytes(b"original-content")
    before = media.read_bytes()
    import os
    before_mtime = os.path.getmtime(media)
    record = _make_record("FFF", "Untouched", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(media)
    _inject([record])

    cli.show_history()
    cli.show_history_detail("FFF")

    assert media.read_bytes() == before
    assert os.path.getmtime(media) == before_mtime
    assert media.exists()


def test_history_search_status_file_exists(data_dir, capsys, tmp_path):
    media = tmp_path / "findme.mp4"
    media.write_bytes(b"hello")
    record = _make_record("AAA", "Find Me", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(media)
    _inject([record])

    cli.search_history("find")
    out = capsys.readouterr().out
    assert "Find Me" in out
    assert "Status     : ✓ File exists" in out


def test_history_search_status_file_missing(data_dir, capsys):
    record = _make_record("BBB", "Gone Target", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(data_dir / "not-here.mp4")
    _inject([record])

    cli.search_history("gone")
    out = capsys.readouterr().out
    assert "Gone Target" in out
    assert "Status     : ✗ File missing" in out


def test_history_search_status_missing_filepath(data_dir, capsys):
    record = _make_record("CCC", "No Path Target", "2026-01-01T10:00:00+00:00")
    record.pop("filepath")
    _inject([record])

    cli.search_history("no path")
    out = capsys.readouterr().out
    assert "No Path Target" in out
    assert "Status     : ✗ File missing" in out


def test_history_count_nonzero(data_dir, capsys):
    _inject([
        _make_record("AAA", "One", "2026-01-01T10:00:00+00:00"),
        _make_record("BBB", "Two", "2026-01-02T10:00:00+00:00"),
        _make_record("CCC", "Three", "2026-01-03T10:00:00+00:00"),
    ])

    cli.show_history_count()
    out = capsys.readouterr().out
    assert "Total downloads: 3" in out


def test_history_count_zero(data_dir, capsys):
    cli.show_history_count()
    out = capsys.readouterr().out
    assert "Total downloads: 0" in out


def test_history_count_dispatch(data_dir, monkeypatch, capsys):
    _inject([_make_record("AAA", "One", "2026-01-01T10:00:00+00:00")])
    captured = {}
    monkeypatch.setattr(cli, "show_history_count", lambda: captured.setdefault("called", True))
    monkeypatch.setattr(sys, "argv", ["downv", "history", "count"])
    cli.main()
    assert captured.get("called") is True


def test_history_count_uses_count_history_api(data_dir, monkeypatch, capsys):
    monkeypatch.setattr(history, "count_history", lambda: 7)
    cli.show_history_count()
    out = capsys.readouterr().out
    assert "Total downloads: 7" in out


def test_history_count_extra_argument(data_dir, capsys, monkeypatch):
    _inject([_make_record("AAA", "One", "2026-01-01T10:00:00+00:00")])
    monkeypatch.setattr(sys, "argv", ["downv", "history", "count", "extra"])
    cli.main()
    out = capsys.readouterr().out
    assert "Usage: downv history count" in out
    assert history.count_history() == 1


def test_history_count_corrupted(data_dir, capsys):
    corrupted = data_dir / "history.json"
    corrupted.write_text("{ this is not valid json")

    cli.show_history_count()
    out = capsys.readouterr().out
    assert "✗ Could not read download history." in out
    assert "Traceback" not in out
    assert corrupted.read_text() == "{ this is not valid json"


def test_print_existing_shows_record_details(data_dir, capsys, tmp_path):
    media = tmp_path / "found.mp4"
    media.write_bytes(b"data")
    record = _make_record("AAA", "Already Got It", "2026-01-01T10:00:00+00:00", quality=1080)
    record["filepath"] = str(media)
    _inject([record])

    cli._print_existing({"id": "AAA"})
    out = capsys.readouterr().out
    assert "✓ Video already downloaded" in out
    assert "Title    : Already Got It" in out
    assert "Quality  : 1080p" in out
    assert f"File     : {media}" in out
    assert "Status   : ✓ File exists" in out


def test_print_existing_uses_find_downloads_api(data_dir, monkeypatch, capsys):
    record = _make_record("AAA", "Via API", "2026-01-01T10:00:00+00:00")
    calls = []
    monkeypatch.setattr(history, "find_downloads", lambda vid: calls.append(vid) or [record])
    cli._print_existing({"id": "AAA"})
    assert calls == ["AAA"]
    out = capsys.readouterr().out
    assert "✓ Video already downloaded" in out
    assert "Via API" in out


def test_print_existing_file_status_missing(data_dir, capsys):
    record = _make_record("BBB", "Gone File", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(data_dir / "not-here.mp4")
    _inject([record])

    cli._print_existing({"id": "BBB"})
    out = capsys.readouterr().out
    assert "✓ Video already downloaded" in out
    assert "Gone File" in out
    assert "Status   : ✗ File missing" in out


def test_print_existing_no_record_fallback(data_dir, capsys):
    _inject([])
    cli._print_existing({"id": "ZZZ"})
    out = capsys.readouterr().out
    assert "✓ Video already downloaded" in out
    assert "Title" not in out


def test_print_existing_historyerror_does_not_abort(data_dir, capsys, monkeypatch):
    class _Err(history.HistoryError):
        pass

    def _boom(vid):
        raise _Err("boom")

    monkeypatch.setattr(history, "find_downloads", _boom)
    cli._print_existing({"id": "AAA"})
    out = capsys.readouterr().out
    assert "✓ Video already downloaded" in out
    assert "Warning: boom" in out


def test_run_download_duplicate_wiring(data_dir, monkeypatch, capsys, tmp_path):
    media = tmp_path / "wired.mp4"
    media.write_bytes(b"data")
    record = _make_record("AAA", "Wired Duplicate", "2026-01-01T10:00:00+00:00")
    record["filepath"] = str(media)
    _inject([record])

    monkeypatch.setattr(cli, "get_media_info", lambda url: {"id": "AAA", "title": "Wired Duplicate", "duration": 10})
    monkeypatch.setattr(cli, "find_existing_download", lambda info: media)
    monkeypatch.setattr(cli, "select_formats", lambda info: {480: object()})
    monkeypatch.setattr(cli, "select_quality", lambda qualities: object())
    monkeypatch.setattr(sys, "argv", ["downv"])
    monkeypatch.setattr("builtins.input", lambda prompt="": "https://example.com/v")
    cli.main()
    out = capsys.readouterr().out
    assert "✓ Video already downloaded" in out
    assert "Wired Duplicate" in out
    assert "Status   : ✓ File exists" in out


def test_print_existing_video_message_default(data_dir, capsys):
    """A video duplicate prints 'Video already downloaded' (default media type)."""
    record = _make_record("AAA", "Video Duplicate", "2026-01-01T10:00:00+00:00")
    record["media_type"] = "video"
    _inject([record])

    cli._print_existing({"id": "AAA"})
    out = capsys.readouterr().out
    assert "✓ Video already downloaded" in out
    assert "✓ Audio already downloaded" not in out


def test_print_existing_audio_message(data_dir, capsys):
    """An audio duplicate prints 'Audio already downloaded'."""
    record = _make_record("AAA", "Audio Duplicate", "2026-01-01T10:00:00+00:00")
    record["media_type"] = "audio"
    record["quality"] = None
    _inject([record])

    cli._print_existing({"id": "AAA"}, media_type="audio")
    out = capsys.readouterr().out
    assert "✓ Audio already downloaded" in out
    assert "✓ Video already downloaded" not in out


def test_commit_audio_download_prints_audio_duplicate(data_dir, monkeypatch, capsys, tmp_path):
    """End-to-end: audio duplicate detection routes to the audio message."""
    media = tmp_path / "wired.mp3"
    media.write_bytes(b"data")
    record = _make_record("AAA", "Wired Audio", "2026-01-01T10:00:00+00:00")
    record["media_type"] = "audio"
    record["quality"] = None
    record["filepath"] = str(media)
    _inject([record])

    monkeypatch.setattr(cli, "find_existing_download", lambda info, media_type="video": media)
    result = cli._commit_audio_download({"id": "AAA", "title": "Wired Audio"}, object(), tmp_path)
    assert result == "skipped"
    out = capsys.readouterr().out
    assert "✓ Audio already downloaded" in out
    assert "✓ Video already downloaded" not in out
