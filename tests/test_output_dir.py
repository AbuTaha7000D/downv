"""Tests for Step 8.3 output directory configuration/override.

Covers ``--output <dir>`` and ``DOWNV_OUTPUT_DIR`` with the required
precedence (``--output`` > ``DOWNV_OUTPUT_DIR`` > built-in default), and
verifies the resolved directory actually reaches the download pipeline for
both standalone videos and playlists.
"""

import os
import sys

import pytest

from downv import cli, paths
from downv.formats import SelectedMediaFormat


def _selected():
    return SelectedMediaFormat(480, "0", None, 1000)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep the DOWNV_OUTPUT_DIR environment variable from leaking between tests."""
    monkeypatch.delenv("DOWNV_OUTPUT_DIR", raising=False)


@pytest.fixture()
def _interactive_standalone(monkeypatch):
    """Drive a single-video interactive download and record where download_media runs."""
    calls = []
    monkeypatch.setattr(sys, "argv", ["downv"])
    monkeypatch.setattr(cli, "prompt_for_url", lambda: "https://example.com/video")
    monkeypatch.setattr(
        cli, "get_media_info", lambda u: {"_type": "video", "title": "T", "id": "v1"}
    )
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)

    def fake_download(info, selected, output_dir):
        calls.append(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / "x.mp4"
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(cli, "download_media", fake_download)
    return calls


# --------------------------------------------------------------------------- #
# 1. Default output directory remains unchanged
# --------------------------------------------------------------------------- #


def test_default_output_dir_unchanged(monkeypatch):
    monkeypatch.delenv("DOWNV_OUTPUT_DIR", raising=False)
    default = paths.get_output_directory()
    resolved = paths.resolve_output_directory(None)
    assert resolved == default


# --------------------------------------------------------------------------- #
# 2/3. --output is accepted and overrides the default (standalone)
# --------------------------------------------------------------------------- #


def test_output_flag_drives_standalone_download(monkeypatch, tmp_path, capsys, _interactive_standalone):
    calls = _interactive_standalone
    override = tmp_path / "cli-out"
    monkeypatch.setattr(sys, "argv", ["downv", "--output", str(override)])
    assert cli.main() == 0
    assert len(calls) == 1
    assert calls[0] == override


# --------------------------------------------------------------------------- #
# 4. DOWNV_OUTPUT_DIR overrides the default
# --------------------------------------------------------------------------- #


def test_env_var_drives_standalone_download(monkeypatch, tmp_path, capsys, _interactive_standalone):
    calls = _interactive_standalone
    override = tmp_path / "env-out"
    monkeypatch.setenv("DOWNV_OUTPUT_DIR", str(override))
    assert cli.main() == 0
    assert len(calls) == 1
    assert calls[0] == override


# --------------------------------------------------------------------------- #
# 5. --output takes precedence over DOWNV_OUTPUT_DIR
# --------------------------------------------------------------------------- #


def test_cli_flag_wins_over_env(monkeypatch, tmp_path, capsys, _interactive_standalone):
    calls = _interactive_standalone
    env_dir = tmp_path / "env-out"
    cli_dir = tmp_path / "cli-out"
    monkeypatch.setenv("DOWNV_OUTPUT_DIR", str(env_dir))
    monkeypatch.setattr(sys, "argv", ["downv", "--output", str(cli_dir)])
    assert cli.main() == 0
    assert len(calls) == 1
    assert calls[0] == cli_dir


# --------------------------------------------------------------------------- #
# 6. Standalone video uses the overridden directory (handled above); relative/~ paths
# --------------------------------------------------------------------------- #


def test_output_flag_expands_home_relative(monkeypatch, capsys, _interactive_standalone):
    calls = _interactive_standalone
    monkeypatch.setattr(sys, "argv", ["downv", "--output", "~/myvideos"])
    assert cli.main() == 0
    assert len(calls) == 1
    assert calls[0] == paths.Path.home() / "myvideos"


# --------------------------------------------------------------------------- #
# 7. Playlist uses <override>/<title>/
# --------------------------------------------------------------------------- #


def test_playlist_uses_override_plus_playlist_dir(monkeypatch, tmp_path, capsys):
    override = tmp_path / "playlist-out"
    monkeypatch.setattr(sys, "argv", ["downv", "--output", str(override)])
    monkeypatch.setattr(cli, "_read_line", lambda p: "y")
    monkeypatch.setattr(cli, "prompt_for_url", lambda: "https://example.com/pl")
    monkeypatch.setattr(cli, "get_media_info", lambda u: {
        "_type": "playlist",
        "title": "newTest",
        "playlist_count": 2,
        "entries": [
            {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
            {"id": "v2", "title": "Two", "webpage_url": "https://e/v2"},
        ],
    })
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: e)
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)

    seen_dirs = []
    monkeypatch.setattr(
        cli, "download_media",
        lambda info, selected, output_dir: seen_dirs.append(output_dir)
        or (output_dir / f"{info.get('id', 'x')}.mp4"),
    )
    assert cli.main() == 0
    playlist_dir = override / "newTest"
    assert len(seen_dirs) == 2
    assert all(d == playlist_dir for d in seen_dirs)


# --------------------------------------------------------------------------- #
# 8. Existing behavior remains unchanged when no override provided (standalone)
# --------------------------------------------------------------------------- #


def test_no_override_uses_default(monkeypatch, tmp_path, capsys, _interactive_standalone):
    calls = _interactive_standalone
    monkeypatch.setattr(paths, "get_output_directory", lambda: tmp_path / "default-dir")
    assert cli.main() == 0
    assert len(calls) == 1
    assert calls[0] == tmp_path / "default-dir"


# --------------------------------------------------------------------------- #
# 9. python -m downv --output routes through the same main() path
# --------------------------------------------------------------------------- #


def test_module_entry_hands_args_to_main(monkeypatch, tmp_path, capsys, _interactive_standalone):
    imptools_calls = _interactive_standalone
    override = tmp_path / "module-out"
    monkeypatch.setattr(sys, "argv", ["downv", "--output", str(override)])
    # ``python -m downv`` executes ``sys.exit(main())``; the same ``main()`` we
    # exercise here reads ``sys.argv`` and honours ``--output``.
    assert cli.main() == 0
    assert imptools_calls == [override]


# --------------------------------------------------------------------------- #
# 10. EOF / KeyboardInterrupt still works with --output
# --------------------------------------------------------------------------- #


def test_eof_with_output_flag_still_cancels(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["downv", "--output", str(tmp_path / "out")])
    monkeypatch.setattr(cli, "prompt_for_url", lambda: None)
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "Download cancelled." in out


def test_keyboard_interrupt_with_output_flag_still_130(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["downv", "--output", str(tmp_path / "out")])

    def raise_interrupt():
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "prompt_for_url", raise_interrupt)
    assert cli.main() == 130