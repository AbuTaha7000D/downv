"""Regression tests for Step 8.1 graceful interactive-input handling.

These lock in the behaviour that fixes the "unavailable/invalid stdin" P0
problems: ``EOFError`` at the URL/confirm/retry prompts must terminate cleanly
instead of raising a traceback or looping, the quality menu must not re-render
forever when ``read_key()`` returns empty/EOF, and interactive input paths must
never touch ``termios``/``tty`` when stdin is not a live TTY.
"""

import io

import pytest

from downv import cli
from downv.downloader import DownloadFailure
from downv.formats import SelectedMediaFormat


def _selected():
    return SelectedMediaFormat(480, "0", None, 1000)


def _endless_eof(*args, **kwargs):
    raise EOFError("EOF when reading a line")


def _selected_info():
    return {
        "title": "Sample",
        "uploader": "Example",
        "duration": 60,
        "formats": [{"format_id": "0", "height": 480, "acodec": "mp4a", "vcodec": "avc1", "filesize": 1000}],
        "webpage_url": "https://example.com/watch?v=v1",
    }


def _quality_menu():
    return {480: SelectedMediaFormat(480, "0", None, 1000)}


# --------------------------------------------------------------------------- #
# URL prompt EOF
# --------------------------------------------------------------------------- #


def test_url_prompt_eof_returns_none(monkeypatch):
    """EOF at the URL prompt returns None (clean termination, no traceback)."""
    monkeypatch.setattr("builtins.input", _endless_eof)
    assert cli.prompt_for_url() is None


def test_url_prompt_empty_line_reprompts_then_accepts(monkeypatch):
    """Correct-empty-input behaviour is preserved: blank lines reprompt,
    then the first non-blank line is returned."""
    calls = []

    def fake_input(p):
        calls.append(p)
        if len(calls) == 1:
            return ""
        return "  https://example.com/video  "

    monkeypatch.setattr("builtins.input", fake_input)
    assert cli.prompt_for_url() == "https://example.com/video"
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# Playlist confirmation EOF -> decline (no media processing)
# --------------------------------------------------------------------------- #


def test_confirm_eof_declines_no_processing(monkeypatch, capsys):
    """EOF at the confirmation prompt is treated as a safe decline."""
    monkeypatch.setattr("builtins.input", _endless_eof)
    info = {"_type": "playlist", "title": "P", "playlist_count": 2, "entries": [{}, {}]}
    assert cli._handle_playlist(info) is False
    captured = capsys.readouterr().out
    assert "Traceback" not in captured


def test_confirm_normal_yes_and_no(monkeypatch):
    """Normal confirmation semantics are unchanged (y/yes confirm, else decline)."""

    def fake_input(p):
        return "\n y \n"

    monkeypatch.setattr("builtins.input", fake_input)
    info = {"_type": "playlist", "title": "P", "playlist_count": 1, "entries": [{}]}
    assert cli._handle_playlist(info) is True

    monkeypatch.setattr("builtins.input", lambda p: "n")
    assert cli._handle_playlist(info) is False


# --------------------------------------------------------------------------- #
# Retry prompt EOF -> decline (no retry)
# --------------------------------------------------------------------------- #


def test_retry_prompt_eof_breaks_no_retry(monkeypatch, tmp_path, capsys):
    """EOF at the retry prompt breaks out with no retry and no traceback."""
    # A fully-resolved item processed with a preselect quality; patching
    # _download_video to fail classifies it "failed" so the retry prompt appears.
    attempts = []
    monkeypatch.setattr(cli, "_download_video", lambda *a, **k: attempts.append(a[0]["id"]) or "failed")
    monkeypatch.setattr("builtins.input", _endless_eof)
    items = [
        {
            "index": 0,
            "entry": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "resolved": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "qualities": {480: _selected()},
        }
    ]

    cli._run_with_retries(items, chosen_height=480, playlist_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Traceback" not in out
    # Initial pass marks the item failed (one attempt); EOF declines the retry
    # so there is no second download attempt and no retry pass is run.
    assert len(attempts) == 1
    assert "Retrying failed/unresolved items..." not in out


def test_retry_prompt_normal_yes_retries(monkeypatch, tmp_path):
    """Normal retry semantics are unchanged: answering yes retries a failed item.

    The initial pass fails once, then answering "y" triggers a retry pass in
    which the same item succeeds (two attempts total) — proving the EOF-break
    change did not disable the real retry flow.
    """
    monkeypatch.setattr("builtins.input", lambda p: "y")
    attempts = []

    def flaky_download(resolved, **k):
        vid = resolved["id"]
        attempts.append(vid)
        return "downloaded" if len(attempts) > 1 else "failed"

    monkeypatch.setattr(cli, "_download_video", flaky_download)
    items = [
        {
            "index": 0,
            "entry": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "resolved": {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            "qualities": {480: _selected()},
        }
    ]

    cli._run_with_retries(items, chosen_height=480, playlist_dir=tmp_path)
    # initial fail + one successful retry
    assert len(attempts) == 2


# --------------------------------------------------------------------------- #
# Quality menu EOF -> clean cancel, no infinite loop, no extra render
# --------------------------------------------------------------------------- #


def test_quality_menu_eof_returns_none_no_infinite_loop(monkeypatch):
    """The core P0 fix: EOF from ``read_key()`` must exit immediately.

    ``read_key`` is mocked to return ``""`` (the actual empty-read EOF
    condition). The menu must return ``None`` without re-entering the render
    loop (no infinite loop, no duplicate menu), and without selecting a
    quality. Reads are counted to prove the menu does not redraw again.
    """
    reads = {"n": 0}

    def counting_read_key():
        reads["n"] += 1
        if reads["n"] > 5:
            raise AssertionError("menu kept reading keys after EOF: infinite loop")
        return ""

    monkeypatch.setattr(cli, "read_key", counting_read_key)
    result = cli.select_quality(_quality_menu())
    assert result is None
    # Only one read happened (the EOF), and we did not re-render/loop.
    assert reads["n"] == 1


def test_quality_menu_eof_no_extra_menu_redraw(monkeypatch, capsys):
    """On EOF the menu erases the current frame and does not draw again."""
    monkeypatch.setattr(cli, "read_key", lambda: "")
    result = cli.select_quality(_quality_menu())
    assert result is None
    # The menu text is drawn exactly once; a second draw would show a second
    # "Available qualities:" header.
    out = capsys.readouterr().out
    assert out.count("Available qualities:") == 1


# --------------------------------------------------------------------------- #
# Quality menu non-TTY stdin
# --------------------------------------------------------------------------- #


def test_read_key_non_tty_returns_eof_no_termios(monkeypatch):
    """read_key on non-TTY stdin returns 'EOF' and never touches termios/tty."""
    import sys
    import termios

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    calls = {"tcgetattr": 0}
    orig_tcgetattr = termios.tcgetattr

    def spy_tcgetattr(fd):
        calls["tcgetattr"] += 1
        return orig_tcgetattr(fd)

    monkeypatch.setattr(termios, "tcgetattr", spy_tcgetattr)
    assert cli.read_key() == "EOF"
    assert calls["tcgetattr"] == 0


def test_quality_menu_non_tty_returns_none_cleanly(monkeypatch, capsys):
    """Quality menu on non-TTY stdin returns None cleanly (no termios crash)."""
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    result = cli.select_quality(_quality_menu())
    assert result is None
    out = capsys.readouterr().out
    assert "Traceback" not in out


# --------------------------------------------------------------------------- #
# Normal quality menu behaviour preserved
# --------------------------------------------------------------------------- #


def test_quality_menu_navigation_and_selection(monkeypatch):
    """Arrow-key navigation and Enter selection behave as before."""
    menu = {
        144: SelectedMediaFormat(144, "0", None, 1000),
        360: SelectedMediaFormat(360, "1", None, 2000),
        720: SelectedMediaFormat(720, "2", None, 3000),
    }
    keys = iter(["DOWN", "DOWN", "ENTER"])

    def fake_read_key():
        return next(keys)

    monkeypatch.setattr(cli, "read_key", fake_read_key)
    selected = cli.select_quality(menu)
    assert selected.height == 720


def test_quality_menu_wraparound_bounds(monkeypatch):
    """Selection wraps within bounds (UP from first -> last)."""
    menu = {
        144: SelectedMediaFormat(144, "0", None, 1000),
        360: SelectedMediaFormat(360, "1", None, 2000),
        720: SelectedMediaFormat(720, "2", None, 3000),
    }
    state = {"key": 0}

    def up_then_enter():
        state["key"] += 1
        return "UP" if state["key"] < 2 else "ENTER"

    monkeypatch.setattr(cli, "read_key", up_then_enter)
    selected = cli.select_quality(menu)
    # From index 0, UP wraps to the last item (720), then ENTER selects it.
    assert selected.height == 720


# --------------------------------------------------------------------------- #
# Standalone download cancellation wiring
# --------------------------------------------------------------------------- #


def test_download_video_quality_menu_cancel_returns_failed(monkeypatch, capsys):
    """Cancelling the standalone quality menu (EOF/non-TTY) cancels the download.

    ``select_quality`` returning None must be interpreted as a user/chained
    cancellation ('failed'), not a download, and must not touch the network.
    """
    info = _selected_info()
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "select_quality", lambda q: None)
    outcome = cli._download_video(info)
    assert outcome == "failed"
    out = capsys.readouterr().out
    assert "Download cancelled." in out


def test_run_download_url_prompt_eof_exits_cleanly(monkeypatch, capsys):
    """EOF at the URL prompt exits _run_download cleanly (no traceback)."""
    monkeypatch.setattr("builtins.input", _endless_eof)
    monkeypatch.setattr(cli, "get_media_info", lambda u: {"_type": "video"})
    cli._run_download()
    out = capsys.readouterr().out
    assert "Download cancelled." in out
    assert "Traceback" not in out


def test_playlist_quality_menu_cancel_aborts_entire_run(monkeypatch, capsys):
    """Cancelling the playlist aggregate quality menu aborts the whole run.

    ``select_quality`` returning None must not fall through to per-item
    prompting; the run must end cleanly with a cancellation message and no
    media processing.
    """
    info = {
        "_type": "playlist",
        "title": "P",
        "playlist_count": 2,
        "entries": [
            {"id": "v1", "title": "One", "webpage_url": "https://e/v1", "formats": []},
            {"id": "v2", "title": "Two", "webpage_url": "https://e/v2", "formats": []},
        ],
    }
    monkeypatch.setattr("builtins.input", lambda p: "y")
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: e)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: None)
    monkeypatch.setattr(cli, "get_media_info", lambda u: info)

    cli._run_download()
    out = capsys.readouterr().out
    assert "Download cancelled." in out
    assert "Playlist item 1" not in out
    assert "Traceback" not in out