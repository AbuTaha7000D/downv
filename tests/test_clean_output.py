"""Tests for Phase 11 clean-CLI-output behaviour.

Locks in that normal downloads suppress yt-dlp's framework chatter (via a muted
logger plus ``quiet``/``noprogress``), that ``--verbose`` re-enables yt-dlp
diagnostics through the ``downv.downloader`` logger (via :func:`set_verbose`),
and that the module-level verbose flag is properly reset between runs.
"""

import logging

import pytest
from pathlib import Path

from downv import downloader
from downv.formats import SelectedAudio, SelectedMediaFormat


def _selected():
    return SelectedMediaFormat(480, "0", None, 1000)


def _audio():
    return SelectedAudio("a1", 1000)


@pytest.fixture(autouse=True)
def _reset_verbose():
    downloader.set_verbose(False)
    yield
    downloader.set_verbose(False)


def _make_options(**kw):
    from downv.downloader import _make_options

    return _make_options(
        "https://example.com/v",
        _selected(),
        Path("/tmp"),
        "base",
        **kw,
    )


def _make_audio_options():
    from downv.downloader import _make_audio_options

    return _make_audio_options(
        "https://example.com/v", _audio(), Path("/tmp"), "base"
    )


# --------------------------------------------------------------------------- #
# 1. Normal mode is clean (quiet + noprogress + muted logger)
# --------------------------------------------------------------------------- #


def test_normal_video_options_are_quiet_and_muted():
    from downv.downloader import _MutedLogger

    opts = _make_options()
    assert opts["quiet"] is True
    assert opts["noprogress"] is True
    assert isinstance(opts["logger"], _MutedLogger)


def test_normal_audio_options_are_quiet_and_muted():
    from downv.downloader import _MutedLogger

    opts = _make_audio_options()
    assert opts["quiet"] is True
    assert opts["noprogress"] is True
    assert isinstance(opts["logger"], _MutedLogger)


def test_normal_video_options_keep_format():
    opts = _make_options()
    assert opts["format"] == "0"
    assert opts["merge_output_format"] == "mp4"
    assert opts["overwrites"] is False


# --------------------------------------------------------------------------- #
# 2. Verbose mode re-enables diagnostics
# --------------------------------------------------------------------------- #


def test_verbose_video_options_use_quiet_logger():
    from downv.downloader import _QuietLogger

    downloader.set_verbose(True)
    opts = _make_options()
    assert opts["quiet"] is False
    assert opts["noprogress"] is False
    assert isinstance(opts["logger"], _QuietLogger)


def test_verbose_audio_options_use_quiet_logger():
    from downv.downloader import _QuietLogger

    downloader.set_verbose(True)
    opts = _make_audio_options()
    assert opts["quiet"] is False
    assert opts["noprogress"] is False
    assert isinstance(opts["logger"], _QuietLogger)


# --------------------------------------------------------------------------- #
# 3. Verbose flag resets so normal runs stay clean
# --------------------------------------------------------------------------- #


def test_verbose_flag_resets_to_false():
    downloader.set_verbose(True)
    assert downloader._VERBOSE is True
    downloader.set_verbose(False)
    assert downloader._VERBOSE is False
    opts = _make_options()
    assert opts["quiet"] is True


def test_set_verbose_coerces_value():
    downloader.set_verbose(1)
    assert downloader._VERBOSE is True
    downloader.set_verbose("")
    assert downloader._VERBOSE is False


# --------------------------------------------------------------------------- #
# 4. Logger behaviour
# --------------------------------------------------------------------------- #


def test_muted_logger_discards_all_messages(caplog):
    from downv.downloader import _MutedLogger

    logger = _MutedLogger()
    with caplog.at_level("DEBUG"):
        logger.debug("d")
        logger.warning("w")
        logger.error("e")
    assert caplog.records == []


def test_quiet_logger_routes_to_downv_logger(caplog):
    import logging

    from downv.downloader import _QuietLogger

    logger = _QuietLogger()
    with caplog.at_level("DEBUG", logger="downv.downloader"):
        logger.debug("debug line")
        logger.warning("warning line")
    assert "debug line" in caplog.text
    assert "warning line" in caplog.text
    # Messages are debug/warning level on the downv.downloader logger.
    assert {r.levelno for r in caplog.records} >= {
        logging.DEBUG,
        logging.WARNING,
    }


# --------------------------------------------------------------------------- #
# 5. cli threads verbose into set_verbose when running downloads
# --------------------------------------------------------------------------- #


def test_run_download_sets_verbose_true(monkeypatch):
    from downv import cli

    calls = []
    monkeypatch.setattr(cli, "set_verbose", lambda v: calls.append(v))
    monkeypatch.setattr(cli, "prompt_for_url", lambda: None)  # cancel early
    logger = logging.getLogger("downv.downloader")
    handlers_before = list(logger.handlers)
    try:
        cli._run_download(verbose=True)
    finally:
        logger.handlers[:] = handlers_before
        logger.setLevel(logging.NOTSET)
    assert calls == [True]


def test_run_download_sets_verbose_false(monkeypatch):
    from downv import cli

    calls = []
    monkeypatch.setattr(cli, "set_verbose", lambda v: calls.append(v))
    monkeypatch.setattr(cli, "prompt_for_url", lambda: None)  # cancel early
    cli._run_download()
    assert calls == [False]


# --------------------------------------------------------------------------- #
# 6. Media-info extraction path (Phase 11 finding 3 regression)
# --------------------------------------------------------------------------- #


def _capture_media_info_options(monkeypatch):
    """Force get_media_info through a fake YoutubeDL that records its options."""
    from downv import extractor

    captured = {}

    class FakeYDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x", "title": "X"}

    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", FakeYDL)
    return captured


def test_media_info_normal_mode_uses_muted_logger(monkeypatch):
    from downv.downloader import _MutedLogger
    from downv import extractor

    captured = _capture_media_info_options(monkeypatch)
    extractor.get_media_info("https://example.com/v")
    opts = captured["options"]
    assert opts["quiet"] is True
    assert isinstance(opts.get("logger"), _MutedLogger)


def test_media_info_verbose_mode_uses_quiet_logger(monkeypatch):
    from downv.downloader import _QuietLogger
    from downv import extractor

    downloader.set_verbose(True)
    captured = _capture_media_info_options(monkeypatch)
    extractor.get_media_info("https://example.com/v")
    opts = captured["options"]
    assert isinstance(opts.get("logger"), _QuietLogger)
