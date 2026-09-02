"""Tests for Phase 9.1 video-chapters support.

When a downloaded video has chapters, DownV asks the user whether to preserve
them and, if accepted, threads a ``preserve_chapters`` flag through the pipeline
so yt-dlp's native ``FFmpegMetadata`` postprocessor embeds them. The prompt is
only shown when chapters exist, only once per playlist, and must not break the
existing mock contracts (1-arg ``_download_video``, 3-arg ``download_media``).
"""

import contextlib
import io
import sys

import pytest

from downv import cli
from downv.downloader import _make_options


def _selected():
    from downv.formats import SelectedMediaFormat

    return SelectedMediaFormat(480, "0", None, 1000)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("DOWNV_OUTPUT_DIR", raising=False)


@pytest.fixture()
def single_video(monkeypatch, tmp_path):
    """Drive a standalone download end to end through mocked pipeline pieces."""
    downloaded = {}

    def fake_get_media_info(url):
        return {"_type": "video", "title": "T", "id": "v1"}

    def fake_select_formats(info):
        return {480: _selected()}

    def fake_select_quality(q):
        return q[480]

    def fake_find_existing(info):
        return None

    def fake_download_media(info, selected, output_dir, **kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "x.mp4"
        path.write_bytes(b"x")
        downloaded["preserve_chapters"] = kwargs.get("preserve_chapters", False)
        return path

    monkeypatch.setattr(cli, "get_media_info", fake_get_media_info)
    monkeypatch.setattr(cli, "select_formats", fake_select_formats)
    monkeypatch.setattr(cli, "select_quality", fake_select_quality)
    monkeypatch.setattr(cli, "find_existing_download", fake_find_existing)
    monkeypatch.setattr(
        cli, "download_media", lambda *a, **k: fake_download_media(*a, **k)
    )
    return downloaded


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main()
    return rc, out.getvalue()


# --------------------------------------------------------------------------- #
# Downloader: _make_options postprocessor wiring
# --------------------------------------------------------------------------- #


def test_make_options_omits_postprocessor_by_default(tmp_path):
    opts = _make_options("https://x/v", _selected(), tmp_path, "base")
    assert "postprocessors" not in opts


def test_make_options_adds_chapter_postprocessor_when_requested(tmp_path):
    opts = _make_options(
        "https://x/v", _selected(), tmp_path, "base", preserve_chapters=True
    )
    assert "postprocessors" in opts
    pp = opts["postprocessors"]
    assert len(pp) == 1
    assert pp[0]["key"] == "FFmpegMetadata"
    assert pp[0]["add_metadata"] is False
    assert pp[0]["add_chapters"] is True
    assert pp[0]["add_infojson"] is False


# --------------------------------------------------------------------------- #
# Single video: prompt shown only when chapters exist
# --------------------------------------------------------------------------- #


def test_single_video_no_chapters_no_prompt(monkeypatch, single_video, capsys):
    calls = []

    def fake_get_media_info(url):
        return {"_type": "video", "title": "T", "id": "v1"}

    monkeypatch.setattr(cli, "get_media_info", fake_get_media_info)
    monkeypatch.setattr(cli, "_read_line", lambda prompt: calls.append(prompt) or None)
    rc, out = _run(monkeypatch, ["downv", "https://x/v"])
    assert rc == 0
    assert calls == []
    assert single_video["preserve_chapters"] is False


def test_single_video_prompt_yes_preserves_chapters(monkeypatch, single_video):
    monkeypatch.setattr(
        cli, "get_media_info", lambda url: _chapter_video()
    )
    monkeypatch.setattr(cli, "_read_line", lambda prompt: "y")
    rc, _ = _run(monkeypatch, ["downv", "https://x/v"])
    assert rc == 0
    assert single_video["preserve_chapters"] is True


def test_single_video_prompt_no_does_not_preserve(monkeypatch, single_video):
    monkeypatch.setattr(cli, "get_media_info", lambda url: _chapter_video())
    monkeypatch.setattr(cli, "_read_line", lambda prompt: "n")
    rc, _ = _run(monkeypatch, ["downv", "https://x/v"])
    assert rc == 0
    assert single_video["preserve_chapters"] is False


def test_single_video_prompt_enter_is_no(monkeypatch, single_video):
    monkeypatch.setattr(cli, "get_media_info", lambda url: _chapter_video())
    monkeypatch.setattr(cli, "_read_line", lambda prompt: "")
    rc, _ = _run(monkeypatch, ["downv", "https://x/v"])
    assert rc == 0
    assert single_video["preserve_chapters"] is False


def test_single_video_prompt_eof_is_no(monkeypatch, single_video):
    monkeypatch.setattr(cli, "get_media_info", lambda url: _chapter_video())
    monkeypatch.setattr(cli, "_read_line", lambda prompt: None)
    rc, _ = _run(monkeypatch, ["downv", "https://x/v"])
    assert rc == 0
    assert single_video["preserve_chapters"] is False


def _chapter_video():
    return {
        "_type": "video",
        "title": "T",
        "id": "v1",
        "chapters": [{"title": "Intro", "start_time": 0}, {"title": "Body", "start_time": 5}],
    }


def test_single_video_prompt_shown_for_chapters(monkeypatch, single_video):
    monkeypatch.setattr(cli, "get_media_info", lambda url: _chapter_video())

    def fake_read_line(prompt):
        calls.append(prompt)
        return "n"

    calls = []
    monkeypatch.setattr(cli, "_read_line", fake_read_line)
    rc, out = _run(monkeypatch, ["downv", "https://x/v"])
    assert rc == 0
    assert any("Download chapters?" in p for p in calls)


# --------------------------------------------------------------------------- #
# Playlist: prompt once, threaded, preserved across retries
# --------------------------------------------------------------------------- #


def _playlist_info(entries, title="My Playlist"):
    return {
        "_type": "playlist",
        "title": title,
        "entries": entries,
    }


def _resolved(vid, title, chapters=None):
    info = {
        "id": vid,
        "title": title,
        "webpage_url": f"https://example.com/watch?v={vid}",
        "url": f"https://example.com/watch?v={vid}",
        "formats": [{"format_id": "0", "height": 480, "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000}],
        "duration": 60,
    }
    if chapters is not None:
        info["chapters"] = chapters
    return info


@pytest.fixture()
def playlist_pipeline(monkeypatch, tmp_path):
    """Mock the interactive playlist flow so we can drive prompts."""

    def fake_plan_playlist(info):
        per_item = []
        for entry in info["entries"]:
            resolved = {**entry}
            per_item.append({"resolved": resolved, "qualities": {480: _selected()}})
        return 480, per_item

    def fake_playlist_output_dir(title, base):
        d = tmp_path / "playlist"
        d.mkdir(parents=True, exist_ok=True)
        return d

    seen = {}

    def fake_select_formats(resolved):
        return {480: _selected()}

    def fake_select_quality(q):
        return q[480]

    def fake_find_existing(info):
        return None

    def fake_download_video(resolved, **kwargs):
        seen["preserve_chapters"] = kwargs.get("preserve_chapters", False)
        return "downloaded"

    monkeypatch.setattr(cli, "get_media_info", lambda url: _playlist_info([]))
    monkeypatch.setattr(cli, "_handle_playlist", lambda info: True)
    monkeypatch.setattr(cli, "_plan_playlist", fake_plan_playlist)
    monkeypatch.setattr(cli, "_playlist_output_dir", fake_playlist_output_dir)
    monkeypatch.setattr(cli, "select_formats", fake_select_formats)
    monkeypatch.setattr(cli, "select_quality", fake_select_quality)
    monkeypatch.setattr(cli, "find_existing_download", fake_find_existing)
    monkeypatch.setattr(cli, "_download_video", fake_download_video)
    return seen


def test_playlist_no_chapters_no_prompt(monkeypatch, playlist_pipeline):
    info = _playlist_info(
        entries=[_resolved("v1", "One"), _resolved("v2", "Two")]
    )
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    calls = []
    monkeypatch.setattr(cli, "_read_line", lambda prompt: calls.append(prompt) or "n")
    rc, _ = _run(monkeypatch, ["downv", "https://x/pl"])
    assert calls == []
    assert playlist_pipeline["preserve_chapters"] is False


def test_playlist_chapters_prompt_once(monkeypatch, playlist_pipeline):
    info = _playlist_info(
        entries=[
            _resolved("v1", "One", chapters=[{"title": "A"}]),
            _resolved("v2", "Two", chapters=[{"title": "B"}]),
        ]
    )
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    calls = []
    monkeypatch.setattr(cli, "_read_line", lambda prompt: calls.append(prompt) or "y")
    rc, _ = _run(monkeypatch, ["downv", "https://x/pl"])
    assert rc == 0
    chapter_prompts = [c for c in calls if "Download chapters" in c]
    assert len(chapter_prompts) == 1
    assert playlist_pipeline["preserve_chapters"] is True


def test_playlist_chapters_no_does_not_preserve(monkeypatch, playlist_pipeline):
    info = _playlist_info(
        entries=[
            _resolved("v1", "One", chapters=[{"title": "A"}]),
            _resolved("v2", "Two"),
        ]
    )
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "_read_line", lambda prompt: "n")
    rc, _ = _run(monkeypatch, ["downv", "https://x/pl"])
    assert rc == 0
    assert playlist_pipeline["preserve_chapters"] is False


def test_playlist_retry_keeps_preference_no_extra_prompt(monkeypatch, playlist_pipeline):
    info = _playlist_info(
        entries=[
            _resolved("v1", "One", chapters=[{"title": "A"}]),
            _resolved("v2", "Two", chapters=[{"title": "B"}]),
        ]
    )
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    calls = []

    def fake_read_line(prompt):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr(cli, "_read_line", fake_read_line)
    rc, _ = _run(monkeypatch, ["downv", "https://x/pl"])
    assert rc == 0
    chapter_prompts = [c for c in calls if "Download chapters" in c]
    assert len(chapter_prompts) == 1
    assert playlist_pipeline["preserve_chapters"] is True