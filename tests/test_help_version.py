"""Tests for Phase 9.1 --help/-h and --version/-V CLI flags.

Both must print to stdout, exit 0, terminate immediately, and never enter the
download flow (no URL prompt, no quality menu, no network, no history/output
changes). ``--version`` reports the authoritative ``downv.__version__``.
"""

import contextlib
import io
import subprocess
import sys

import pytest

from downv import cli, __version__


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("DOWNV_OUTPUT_DIR", raising=False)


def _argv(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["downv", *args])


def _run():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main()
    return rc, out.getvalue()


def _spy_download_flow(monkeypatch):
    """Spy on the earliest entry points of the download flow.

    If help/version enters the download pipeline, one of these is called, which
    must never happen.
    """
    calls = []

    def spy(*a, **k):
        calls.append(1)
        raise AssertionError("download flow entered by help/version")

    monkeypatch.setattr(cli, "prompt_for_url", spy)
    monkeypatch.setattr(cli, "get_media_info", spy)
    return calls


# --------------------------------------------------------------------------- #
# --help / -h
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_flag_exits_zero(monkeypatch, flag):
    _argv(monkeypatch, [flag])
    rc, out = _run()
    assert rc == 0
    assert "Usage:" in out
    assert "downv [OPTIONS] [URL]" in out
    assert "downv history <COMMAND>" in out


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_does_not_enter_download_flow(monkeypatch, flag):
    calls = _spy_download_flow(monkeypatch)
    _argv(monkeypatch, [flag])
    rc, _ = _run()
    assert rc == 0
    assert calls == []


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_documents_options(monkeypatch, flag):
    _argv(monkeypatch, [flag])
    rc, out = _run()
    assert rc == 0
    assert "-h, --help" in out
    assert "-V, --version" in out
    assert "-v, --verbose" in out
    assert "--output DIR" in out
    assert "--output=DIR" in out


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_documents_history_and_exit_codes(monkeypatch, flag):
    _argv(monkeypatch, [flag])
    rc, out = _run()
    assert rc == 0
    for sub in ("count", "search", "remove", "clear", "detail"):
        assert f"history {sub}" in out
    assert "0  Success" in out
    assert "1  Error" in out
    assert "130 Interrupted" in out


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_documents_requirements(monkeypatch, flag):
    _argv(monkeypatch, [flag])
    rc, out = _run()
    assert rc == 0
    assert "Python >= 3.10" in out
    assert "yt-dlp" in out
    assert "FFmpeg is required when the selected format needs merging" in out


def test_help_with_url_still_exits_zero(monkeypatch):
    """Standard CLI behavior: --help wins over a trailing URL and exits 0."""
    calls = _spy_download_flow(monkeypatch)
    _argv(monkeypatch, ["--help", "https://example.com/v"])
    rc, out = _run()
    assert rc == 0
    assert "Usage:" in out
    assert calls == []


def test_help_with_mixed_flags_exits_zero(monkeypatch):
    _argv(monkeypatch, ["-v", "--output", "dir", "--help"])
    rc, out = _run()
    assert rc == 0
    assert "Usage:" in out


# --------------------------------------------------------------------------- #
# --version / -V
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_exits_zero(monkeypatch, flag):
    _argv(monkeypatch, [flag])
    rc, out = _run()
    assert rc == 0
    assert out.strip() == f"DownV {__version__}"


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_does_not_enter_download_flow(monkeypatch, flag):
    calls = _spy_download_flow(monkeypatch)
    _argv(monkeypatch, [flag])
    rc, _ = _run()
    assert rc == 0
    assert calls == []


def test_version_with_url_still_exits_zero(monkeypatch):
    calls = _spy_download_flow(monkeypatch)
    _argv(monkeypatch, ["--version", "https://example.com/v"])
    rc, out = _run()
    assert rc == 0
    assert out.strip() == f"DownV {__version__}"
    assert calls == []


def test_version_uses_authoritative_source():
    assert __version__ == cli.__version__
    assert __version__ == "0.1.0"


# --------------------------------------------------------------------------- #
# Module invocation: python -m downv
# --------------------------------------------------------------------------- #


def test_module_help():
    proc = subprocess.run(
        [sys.executable, "-m", "downv", "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Usage:" in proc.stdout
    assert proc.stderr == ""


def test_module_version():
    proc = subprocess.run(
        [sys.executable, "-m", "downv", "--version"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"DownV {__version__}"
    assert proc.stderr == ""


# --------------------------------------------------------------------------- #
# Unknown options remain errors (unchanged contract)
# --------------------------------------------------------------------------- #


def test_unknown_option_still_errors(monkeypatch):
    _argv(monkeypatch, ["--bogus"])
    rc, _ = _run()
    assert rc == 1