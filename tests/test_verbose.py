"""Tests for Step 8.5 verbose/debug CLI mode (-v / --verbose).

Verbose mode adds ``[DEBUG]`` diagnostics and must not change any normal
(verbose-disabled) behavior, the output-directory precedence, the exit-code
contract, or the history subcommands.
"""

import contextlib
import io
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
def data_dir(tmp_path, monkeypatch):
    from downv import history as history_mod

    monkeypatch.setattr(history_mod, "get_data_directory", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def _single_video(monkeypatch, tmp_path):
    """Drive a standalone download end to end through the real pipeline mocks."""
    monkeypatch.setattr(
        cli, "get_media_info", lambda u: {"_type": "video", "title": "T", "id": "v1"}
    )
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected()})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)

    def fake_download(info, selected, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / "x.mp4"
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(cli, "download_media", fake_download)


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main()
    return rc, out.getvalue()


# --------------------------------------------------------------------------- #
# 1/2/3. -v and --verbose enable verbose; equivalent
# --------------------------------------------------------------------------- #


def test_short_flag_runs_verbose(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "-v", "https://example.com/v"])
    assert rc == 0
    assert "[DEBUG] Verbose mode enabled" in out


def test_long_flag_runs_verbose(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "--verbose", "https://example.com/v"])
    assert rc == 0
    assert "[DEBUG] Verbose mode enabled" in out


def test_short_and_long_equivalent(monkeypatch, _single_video):
    outs = []
    for flag in ("-v", "--verbose"):
        rc, out = _run(monkeypatch, ["downv", flag, "https://example.com/v"])
        assert rc == 0
        outs.append(out)
    # Identical CLI state -> identical output.
    assert outs[0] == outs[1]


# --------------------------------------------------------------------------- #
# 4. Normal mode does NOT print [DEBUG]
# --------------------------------------------------------------------------- #


def test_normal_mode_no_debug(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "https://example.com/v"])
    assert rc == 0
    assert "[DEBUG]" not in out


# --------------------------------------------------------------------------- #
# 5. Verbose prints parsed state
# --------------------------------------------------------------------------- #


def test_verbose_prints_parsed_state(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "-v", "https://example.com/v"])
    assert rc == 0
    assert "[DEBUG] Verbose mode enabled" in out
    assert "[DEBUG] URL source: command line" in out
    assert "[DEBUG] Media type: single video" in out
    assert "[DEBUG] Selected quality: 480p" in out


def test_verbose_interactive_url_source(monkeypatch, _single_video):
    monkeypatch.setattr(cli, "prompt_for_url", lambda: "https://example.com/v")
    rc, out = _run(monkeypatch, ["downv", "-v"])
    assert rc == 0
    assert "[DEBUG] URL source: interactive" in out


# --------------------------------------------------------------------------- #
# 6/7/8. Verbose with positional URL / --output / --output=
# --------------------------------------------------------------------------- #


def test_verbose_with_output_flag(monkeypatch, tmp_path, _single_video):
    override = tmp_path / "out"
    rc, out = _run(monkeypatch, ["downv", "-v", "--output", str(override), "https://example.com/v"])
    assert rc == 0
    assert f"[DEBUG] Output directory: {override}" in out


def test_verbose_with_output_equals(monkeypatch, tmp_path, _single_video):
    override = tmp_path / "out"
    rc, out = _run(monkeypatch, ["downv", "--verbose", f"--output={override}", "https://example.com/v"])
    assert rc == 0
    assert f"[DEBUG] Output directory: {override}" in out


# --------------------------------------------------------------------------- #
# 9. verbose + env preserves precedence
# --------------------------------------------------------------------------- #


def test_verbose_env_precedence(monkeypatch, tmp_path, _single_video):
    monkeypatch.setenv("DOWNV_OUTPUT_DIR", str(tmp_path / "env-out"))
    cli_dir = tmp_path / "cli-out"
    rc, out = _run(monkeypatch, ["downv", "--verbose", "--output", str(cli_dir), "https://example.com/v"])
    assert rc == 0
    assert f"[DEBUG] Output directory: {cli_dir}" in out
    assert "env-out" not in out


def test_verbose_env_only(monkeypatch, tmp_path, _single_video):
    monkeypatch.setenv("DOWNV_OUTPUT_DIR", str(tmp_path / "env-only"))
    rc, out = _run(monkeypatch, ["downv", "-v", "https://example.com/v"])
    assert rc == 0
    assert f"[DEBUG] Output directory: {tmp_path / 'env-only'}" in out


# --------------------------------------------------------------------------- #
# 10. verbose can appear before/after --output
# --------------------------------------------------------------------------- #


def test_verbose_after_output(monkeypatch, tmp_path, _single_video):
    override = tmp_path / "out"
    rc, out = _run(monkeypatch, ["downv", "--output", str(override), "-v", "https://example.com/v"])
    assert rc == 0
    assert "[DEBUG] Verbose mode enabled" in out


def test_verbose_interleaved(monkeypatch, tmp_path, _single_video):
    override = tmp_path / "out"
    rc, out = _run(
        monkeypatch,
        ["downv", "--verbose", "--output", str(override), "-v", "https://example.com/v"],
    )
    assert rc == 0
    assert "[DEBUG] Verbose mode enabled" in out


# --------------------------------------------------------------------------- #
# 11/12/13. usage errors still exit 1
# --------------------------------------------------------------------------- #


def test_multiple_urls_exit_1(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "https://a", "https://b"])
    assert rc == 1
    assert "unexpected extra arguments" in out


def test_unknown_option_exit_1(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "--does-not-exist", "https://a"])
    assert rc == 1
    assert "unknown option" in out


def test_missing_output_value_exit_1(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "--output"])
    assert rc == 1


def test_output_followed_by_verbose_exit_1(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "--output", "--verbose", "https://a"])
    assert rc == 1


# --------------------------------------------------------------------------- #
# 14. python -m downv -v URL path valid
# --------------------------------------------------------------------------- #


def test_module_entry_with_verbose(monkeypatch, _single_video):
    rc, out = _run(monkeypatch, ["downv", "-v", "https://example.com/v"])
    assert rc == 0
    assert "[DEBUG]" in out


# --------------------------------------------------------------------------- #
# 15. Ctrl+C still returns 130 without traceback
# --------------------------------------------------------------------------- #


def test_ctrl_c_returns_130(monkeypatch, _single_video):
    monkeypatch.setattr(cli, "prompt_for_url", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    rc, out = _run(monkeypatch, ["downv", "-v"])
    assert rc == 130
    assert "Download cancelled." in out
    assert "Traceback" not in out


# --------------------------------------------------------------------------- #
# 16. history commands still work with and without verbose
# --------------------------------------------------------------------------- #


def test_history_still_works(monkeypatch, data_dir):
    rc, out = _run(monkeypatch, ["downv", "history"])
    assert rc == 0


def test_history_count_with_verbose_flag(monkeypatch, data_dir):
    rc, out = _run(monkeypatch, ["downv", "-v", "history", "count"])
    assert rc == 0