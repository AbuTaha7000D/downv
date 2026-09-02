"""Tests for Step 8.4 non-interactive single-video CLI mode.

A single positional URL argument is taken directly (no ``Enter URL:`` prompt),
while ``downv`` with no URL keeps the interactive prompt. Also covers the
combination with the Step 8.3 ``--output`` flag and environment variable, plus
usage-error handling for malformed invocations. Playlist positional URLs still
run the normal playlist flow.
"""

import sys

import pytest

from downv import cli


def _selected():
    from downv.formats import SelectedMediaFormat

    return SelectedMediaFormat(480, "0", None, 1000)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("DOWNV_OUTPUT_DIR", raising=False)


@pytest.fixture()
def _single_video(monkeypatch, tmp_path):
    """Mock a standalone single-video download, recording the URL and output dirs."""
    calls = {"urls": [], "dirs": []}

    def fake_get_media_info(url):
        calls["urls"].append(url)
        return {"_type": "video", "title": "T", "id": "v1"}

    monkeypatch.setattr(cli, "get_media_info", fake_get_media_info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)

    def fake_download(info, selected, output_dir):
        calls["dirs"].append(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / "x.mp4"
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(cli, "download_media", fake_download)
    return calls


# --------------------------------------------------------------------------- #
# 1. no URL -> interactive mode (prompt shown)
# --------------------------------------------------------------------------- #


def test_no_url_is_interactive(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv"])
    prompt_calls = []
    monkeypatch.setattr(cli, "prompt_for_url", lambda: prompt_calls.append(1) or "https://example.com/video")
    assert cli.main() == 0
    # No URL argument means interactive mode: the URL prompt is invoked.
    assert prompt_calls == [1]
    assert _single_video["urls"] == ["https://example.com/video"]


# --------------------------------------------------------------------------- #
# 2/3. single positional URL -> non-interactive, goes to download flow
# --------------------------------------------------------------------------- #


def test_single_positional_url_skips_prompt(monkeypatch, capsys, _single_video):
    url = "https://example.com/video"
    monkeypatch.setattr(sys, "argv", ["downv", url])
    prompt_calls = []
    monkeypatch.setattr(cli, "prompt_for_url", lambda: prompt_calls.append(1) or None)
    assert cli.main() == 0
    assert prompt_calls == []
    assert _single_video["urls"] == [url]


# --------------------------------------------------------------------------- #
# 4. URL prompt not shown in positional mode
# --------------------------------------------------------------------------- #


def test_no_url_prompt_when_url_argument(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "https://example.com/video"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "Enter URL:" not in out


# --------------------------------------------------------------------------- #
# 5. --output DIR URL
# --------------------------------------------------------------------------- #


def test_output_flag_with_positional_url(monkeypatch, tmp_path, capsys, _single_video):
    override = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["downv", "--output", str(override), "https://example.com/video"])
    assert cli.main() == 0
    assert _single_video["urls"] == ["https://example.com/video"]
    assert _single_video["dirs"] == [override]


# --------------------------------------------------------------------------- #
# 6. --output=DIR URL
# --------------------------------------------------------------------------- #


def test_output_equals_flag_with_positional_url(monkeypatch, tmp_path, capsys, _single_video):
    override = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["downv", f"--output={override}", "https://example.com/video"])
    assert cli.main() == 0
    assert _single_video["dirs"] == [override]


# --------------------------------------------------------------------------- #
# 7/8. DOWNV_OUTPUT_DIR + URL; CLI still beats ENV
# --------------------------------------------------------------------------- #


def test_env_var_with_positional_url(monkeypatch, tmp_path, capsys, _single_video):
    override = tmp_path / "env-out"
    monkeypatch.setenv("DOWNV_OUTPUT_DIR", str(override))
    monkeypatch.setattr(sys, "argv", ["downv", "https://example.com/video"])
    assert cli.main() == 0
    assert _single_video["dirs"] == [override]


def test_output_flag_wins_over_env_with_positional_url(monkeypatch, tmp_path, capsys, _single_video):
    monkeypatch.setenv("DOWNV_OUTPUT_DIR", str(tmp_path / "env-out"))
    cli_dir = tmp_path / "cli-out"
    monkeypatch.setattr(sys, "argv", ["downv", "--output", str(cli_dir), "https://example.com/video"])
    assert cli.main() == 0
    assert _single_video["dirs"] == [cli_dir]


# --------------------------------------------------------------------------- #
# 9/10. usage errors -> clean message + non-zero exit
# --------------------------------------------------------------------------- #


def test_two_positional_urls_is_error(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "https://a", "https://b"])
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "unexpected extra arguments" in out
    assert _single_video["urls"] == []


def test_missing_output_value_is_error(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "--output"])
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "--output requires a directory path" in out


def test_output_followed_by_option_is_error(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "--output", "--verbose"])
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "--output requires a directory path" in out


# --------------------------------------------------------------------------- #
# 11/12. EOF and KeyboardInterrupt unchanged
# --------------------------------------------------------------------------- #


def test_eof_with_no_url_still_cancels(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv"])
    monkeypatch.setattr(cli, "prompt_for_url", lambda: None)
    assert cli.main() == 0
    assert "Download cancelled." in capsys.readouterr().out


def test_keyboard_interrupt_with_no_url_still_130(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv"])

    def raise_interrupt():
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "prompt_for_url", raise_interrupt)
    assert cli.main() == 130


# --------------------------------------------------------------------------- #
# 13. python -m downv URL works through the same main() path
# --------------------------------------------------------------------------- #


def test_module_entry_hands_positional_url_to_main(monkeypatch, capsys, _single_video):
    url = "https://example.com/video"
    monkeypatch.setattr(sys, "argv", ["downv", url])
    # ``python -m downv`` executes ``sys.exit(main())`` with the same argv.
    assert cli.main() == 0
    assert _single_video["urls"] == [url]


# --------------------------------------------------------------------------- #
# 14. positional playlist URL still runs the playlist flow
# --------------------------------------------------------------------------- #


def test_positional_playlist_url_still_detects_playlist(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["downv", "https://example.com/playlist"])
    monkeypatch.setattr(cli, "get_media_info", lambda u: {
        "_type": "playlist",
        "title": "P",
        "playlist_count": 1,
        "entries": [{"id": "v1", "title": "One", "webpage_url": "https://e/v1"}],
    })
    monkeypatch.setattr(cli, "_read_line", lambda p: "y")  # confirm playlist
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: e)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)

    seen = []
    monkeypatch.setattr(
        cli, "download_media",
        lambda info, selected, output_dir: seen.append(output_dir)
        or (output_dir / "x.mp4"),
    )
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "Enter URL:" not in out
    assert "Playlist detected" in out
    assert len(seen) == 1