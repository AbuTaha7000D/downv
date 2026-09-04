"""Regression tests for Step 8.2 clean cancellation + top-level error boundary.

These lock in the behaviour that Ctrl+C (``KeyboardInterrupt``) and unexpected
top-level failures terminate DownV with a concise user-facing message and a
clean exit status instead of a raw Python traceback, while preserving playlist
progress and never writing fake history/success records for interrupted work.
"""

import sys

import pytest

from downv import cli, history
from downv.formats import SelectedMediaFormat


def _selected():
    return SelectedMediaFormat(480, "0", None, 1000)


def _raise_interrupt(*args, **kwargs):
    raise KeyboardInterrupt()


def _report_total_matches_outcomes(report: str) -> bool:
    """True when the playlist report satisfies Total == sum of the five outcomes."""
    import re

    values = {}
    for line in report.splitlines():
        for key in ("Total", "Downloaded", "Skipped", "Failed", "Unresolved"):
            m = re.match(rf"^\s*{key}\s*:\s*(\d+)\s*$", line)
            if m:
                values[key] = int(m.group(1))
    if set(values) != {"Total", "Downloaded", "Skipped", "Failed", "Unresolved"}:
        return False
    return values["Total"] == (
        values["Downloaded"] + values["Skipped"] + values["Failed"] + values["Unresolved"]
    )


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "get_data_directory", lambda: tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# A. Main CLI KeyboardInterrupt -> clean cancellation, exit 130
# --------------------------------------------------------------------------- #


def _no_args(monkeypatch):
    """Force the CLI into the no-arg interactive download flow."""
    monkeypatch.setattr(sys, "argv", ["downv"])


def test_main_keyboard_interrupt_returns_130(monkeypatch, capsys):
    """Ctrl+C during the URL prompt exits cleanly with code 130, no traceback."""
    _no_args(monkeypatch)
    monkeypatch.setattr("builtins.input", _raise_interrupt)
    rc = cli.main()
    assert rc == 130
    out = capsys.readouterr().out
    assert "Download cancelled." in out
    assert "Traceback" not in out


def test_main_keyboard_interrupt_single_cancel_message(monkeypatch, capsys):
    """Only one cancellation message; Ctrl+C is not turned into an Error: line."""
    _no_args(monkeypatch)
    monkeypatch.setattr("builtins.input", _raise_interrupt)
    cli.main()
    out = capsys.readouterr().out
    assert out.count("Download cancelled.") == 1
    assert out.count("Error:") == 0


# --------------------------------------------------------------------------- #
# B. Quality menu KeyboardInterrupt
# --------------------------------------------------------------------------- #


def test_quality_menu_keyboard_interrupt_propagates(monkeypatch):
    """Ctrl+C in the quality menu clears the menu and re-raises (no selection)."""
    cli.read_key = _raise_interrupt
    with pytest.raises(KeyboardInterrupt):
        cli.select_quality({480: _selected()})


def test_quality_menu_keyboard_interrupt_clears_menu(monkeypatch, capsys):
    """On Ctrl+C the rendered menu is erased (its header is cleared)."""
    cli.read_key = _raise_interrupt
    with pytest.raises(KeyboardInterrupt):
        cli.select_quality({480: _selected()})
    out = capsys.readouterr().out
    # The menu was drawn once, then erased via \033[2K clear-line sequences.
    assert out.count("Available qualities:") == 1
    assert "\033[2K" in out


# --------------------------------------------------------------------------- #
# C. Playlist KeyboardInterrupt
# --------------------------------------------------------------------------- #


def test_playlist_interrupt_stops_processing_no_fake_categories(monkeypatch, capsys):
    """Ctrl+C mid-playlist stops immediately, does not process later items, and
    does not mark the interrupted item as Failed or Unresolved."""
    state = {"calls": 0}

    def flaky_download(resolved, **k):
        state["calls"] += 1
        if state["calls"] == 1:
            return "downloaded"
        raise KeyboardInterrupt()  # second item interrupted

    monkeypatch.setattr(cli, "_download_video", flaky_download)
    items = [
        {
            "index": 0,
            "entry": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "resolved": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "qualities": {480: _selected()},
        },
        {
            "index": 1,
            "entry": {"id": "v2", "title": "Two", "webpage_url": "https://e/v2"},
            "resolved": {"id": "v2", "title": "Two", "webpage_url": "https://e/v2"},
            "qualities": {480: _selected()},
        },
    ]

    with pytest.raises(KeyboardInterrupt):
        cli._process_items(items, chosen_height=480, playlist_dir=None, label="Playlist item", known_count=2)

    # Only 2 download attempts (item 1 + interrupted item 2); item 3 would be a
    # third call and must never run.
    assert state["calls"] == 2
    out = capsys.readouterr().out
    # No intermediate cancellation message; the boundary emits the single one.
    assert "Download interrupted." not in out
    # Only the completed item is tallied; the interrupted item is not counted.
    assert "Total      : 1" in out
    assert "Downloaded : 1" in out
    assert "Skipped    : 0" in out
    assert "Failed     : 0" in out
    assert "Unresolved : 0" in out
    # Playlist report invariant is preserved: Total == sum of the five outcomes.
    assert _report_total_matches_outcomes(out)


def test_playlist_interrupt_does_not_start_retry_round(monkeypatch, capsys):
    """A Ctrl+C during the initial playlist pass must not reach a retry prompt."""
    def flaky_download(resolved, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_download_video", flaky_download)
    monkeypatch.setattr("builtins.input", lambda p: "retry never offered")
    items = [
        {
            "index": 0,
            "entry": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "resolved": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "qualities": {480: _selected()},
        }
    ]

    with pytest.raises(KeyboardInterrupt):
        cli._run_with_retries(items, chosen_height=480, playlist_dir=None, known_count=1)
    captured = capsys.readouterr().out
    assert "Retry failed/unresolved items?" not in captured
    assert "Retrying" not in captured


def test_playlist_interrupt_on_first_item_no_misleading_summary(monkeypatch, capsys):
    """Ctrl+C while processing the very first item prints no playlist summary
    (nothing has been classified), and the boundary emits the single message."""
    def interrupt_download(resolved, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_download_video", interrupt_download)
    items = [
        {
            "index": 0,
            "entry": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "resolved": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "qualities": {480: _selected()},
        }
    ]

    with pytest.raises(KeyboardInterrupt):
        cli._process_items(items, chosen_height=480, playlist_dir=None, label="Playlist item", known_count=1)
    out = capsys.readouterr().out
    # No misleading summary with Total: 1; nothing was classified.
    assert "Playlist complete" not in out
    assert "Total      : 1" not in out


def test_playlist_interrupt_via_main_single_cancel_message(monkeypatch, capsys):
    """End-to-end playlist Ctrl+C yields exactly one ``Download cancelled.`` and
    no traceback, and stops before the next item / retry prompt."""
    _no_args(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda p: "y")
    info = {
        "_type": "playlist",
        "title": "P",
        "playlist_count": 2,
        "entries": [
            {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            {"id": "v2", "title": "Two", "webpage_url": "https://e/v2"},
        ],
    }
    monkeypatch.setattr(cli, "get_media_info", lambda u: info)
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: e)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])

    state = {"calls": 0}

    def interrupt_download(resolved, **k):
        state["calls"] += 1
        if state["calls"] == 1:
            return "downloaded"  # first item completes
        raise KeyboardInterrupt()  # second item interrupted

    monkeypatch.setattr(cli, "_download_video", interrupt_download)
    rc = cli.main()
    assert rc == 130
    out = capsys.readouterr().out
    # Exactly one cancellation message.
    assert out.count("Download cancelled.") == 1
    assert "Download interrupted." not in out
    assert "Traceback" not in out
    # No retry prompt, no third item.
    assert "Retry failed/unresolved items?" not in out
    assert state["calls"] == 2  # item1 completed + item2 interrupted
    # Only the completed first item is tallied.
    assert "Playlist complete" in out
    assert _report_total_matches_outcomes(out)
    assert "Downloaded : 1" in out


# --------------------------------------------------------------------------- #
# D. Standalone download KeyboardInterrupt
# --------------------------------------------------------------------------- #


def test_standalone_download_interrupt_no_success_no_history(monkeypatch, data_dir, capsys):
    """Ctrl+C during a standalone download returns cancel, never a success, and
    never writes a fake history record."""
    recorded = []

    def spy_record(*args, **kwargs):
        recorded.append(args)

    monkeypatch.setattr(history, "record_download", spy_record)

    def interrupted_download(info, selected, output_dir):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "download_media", interrupted_download)
    monkeypatch.setattr(cli, "find_existing_download", lambda info: None)
    info = {
        "title": "Demo",
        "uploader": "Example",
        "duration": 60,
        "formats": [{"format_id": "0", "height": 480, "acodec": "mp4a", "vcodec": "avc1", "filesize": 1000}],
        "webpage_url": "https://example.com/watch?v=v1",
        "id": "v1",
    }

    with pytest.raises(KeyboardInterrupt):
        cli._download_video(info)

    assert recorded == []


def test_standalone_download_interrupt_via_main_returns_130(monkeypatch, data_dir, capsys):
    """A Ctrl+C during a standalone download reaches the CLI boundary as 130 and
    prints exactly one cancellation message."""
    _no_args(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda p: "https://example.com/video")
    monkeypatch.setattr(cli, "get_media_info", lambda u: {"_type": "video", "title": "T", "id": "v1"})
    monkeypatch.setattr(cli, "_download_video", _raise_interrupt)
    rc = cli.main()
    assert rc == 130
    out = capsys.readouterr().out
    assert out.count("Download cancelled.") == 1
    assert "Traceback" not in out


# --------------------------------------------------------------------------- #
# E. Top-level unexpected exception boundary
# --------------------------------------------------------------------------- #


def test_main_unexpected_exception_returns_1(monkeypatch, capsys):
    """An unexpected exception yields a concise Error: line, exit 1, no traceback."""
    _no_args(monkeypatch)

    def explode(p):
        raise RuntimeError("internal boom")

    monkeypatch.setattr("builtins.input", explode)
    monkeypatch.setattr(cli, "get_media_info", lambda u: {"_type": "video"})
    rc = cli.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert "Error: internal boom" in out
    assert "Traceback" not in out


def test_main_unexpected_exception_not_cancelled_message(monkeypatch, capsys):
    """Ctrl+C and exceptions are handled distinctly: a generic failure must not
    print the cancellation message."""
    _no_args(monkeypatch)

    def explode(p):
        raise RuntimeError("boom")

    monkeypatch.setattr("builtins.input", explode)
    monkeypatch.setattr(cli, "get_media_info", lambda u: {"_type": "video"})
    cli.main()
    out = capsys.readouterr().out
    assert "Download cancelled." not in out
    assert "Error: boom" in out


# --------------------------------------------------------------------------- #
# F. Both entry points share the boundary
# --------------------------------------------------------------------------- #


def test_module_entry_routes_through_main():
    """``python -m downv`` (``downv/__main__.py``) delegates to ``cli.main`` and
    exits with its return code, so it shares the same graceful boundary."""
    import os

    module_path = os.path.join(os.path.dirname(cli.__file__), "__main__.py")
    with open(module_path) as f:
        source = f.read()
    assert "from downv.cli import main" in source
    assert "sys.exit(main())" in source


def test_console_entry_point_is_cli_main():
    """The installed console script ``downv`` targets ``downv.cli:main``."""
    import importlib.metadata

    target = None
    for ep in importlib.metadata.entry_points().select(group="console_scripts"):
        if ep.name == "downv":
            target = ep.value
            break
    assert target == "downv.cli:main"


def test_python_m_downv_no_traceback_on_error(monkeypatch):
    """``python -m downv`` reaches the same boundary: Ctrl+C yields 130."""
    _no_args(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda p: "https://example.com/video")
    monkeypatch.setattr(cli, "get_media_info", lambda u: {"_type": "video"})
    monkeypatch.setattr(cli, "_download_video", _raise_interrupt)
    rc = cli.main()
    assert rc == 130


def test_unexpected_exception_traceback_in_verbose(monkeypatch, capsys):
    """In --verbose mode an unexpected internal error stays debuggable with a traceback."""
    from downv import downloader

    monkeypatch.setattr(sys, "argv", ["downv", "--verbose"])

    def explode(p):
        raise RuntimeError("internal boom")

    monkeypatch.setattr("builtins.input", explode)
    monkeypatch.setattr(cli, "get_media_info", lambda u: {"_type": "video"})
    try:
        rc = cli.main()
    finally:
        downloader.set_verbose(False)
    captured = capsys.readouterr()
    assert rc == 1
    assert "Error: internal boom" in captured.out
    assert "Traceback (most recent call last)" in captured.err
