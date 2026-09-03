"""Tests for Step 9.2 non-interactive quality selection via ``--quality``.

Covers CLI parsing/validation of ``--quality``, the interplay with the existing
interactive selector (which must remain for URLs without ``--quality``), the
format-selection semantics in :func:`formats.pick_quality_by_height`, and the
playlist behaviour when ``--quality`` is supplied.
"""

import sys

import pytest

from downv import cli
from downv.formats import SelectedMediaFormat, pick_quality_by_height, select_formats


def _selected(height=480):
    return SelectedMediaFormat(height, str(height), None, 1000)


def _info(formats):
    return {"_type": "video", "title": "T", "id": "v1", "formats": formats}


def _fmt(fmt_id, height, vcodec="avc1", acodec="mp4a", filesize=1000):
    return {
        "format_id": fmt_id,
        "height": height,
        "vcodec": vcodec,
        "acodec": acodec,
        "filesize": filesize,
    }


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("DOWNV_OUTPUT_DIR", raising=False)


@pytest.fixture()
def _single_video(monkeypatch, tmp_path):
    """Mock a standalone single-video download, recording the URL and quality."""
    calls = {"urls": [], "qualities": []}

    def fake_get_media_info(url):
        calls["urls"].append(url)
        return {
            "_type": "video",
            "title": "T",
            "id": "v1",
            "formats": [
                _fmt("2160", 2160),
                _fmt("1080", 1080),
                _fmt("720", 720),
                _fmt("480", 480),
            ],
        }

    monkeypatch.setattr(cli, "get_media_info", fake_get_media_info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {
        h: _selected(h) for h in (2160, 1080, 720, 480)
    })
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)

    def fake_download(info, selected, output_dir):
        calls["qualities"].append(selected.height)
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / "x.mp4"
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(cli, "download_media", fake_download)
    return calls


# --------------------------------------------------------------------------- #
# 1. CLI parsing: --quality forms
# --------------------------------------------------------------------------- #


def test_quality_flag_passed_to_download(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "--quality", "1080", "https://example.com/video"])
    assert cli.main() == 0
    assert _single_video["urls"] == ["https://example.com/video"]
    assert _single_video["qualities"] == [1080]


def test_quality_flag_before_and_after_url(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "https://example.com/video", "--quality", "720"])
    assert cli.main() == 0
    assert _single_video["qualities"] == [720]


def test_quality_equals_form(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "--quality=1080", "https://example.com/video"])
    assert cli.main() == 0
    assert _single_video["qualities"] == [1080]


def test_quality_coexists_with_output(monkeypatch, tmp_path, capsys, _single_video):
    override = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "downv", "--quality", "720", "--output", str(override), "https://example.com/video",
    ])
    assert cli.main() == 0
    assert _single_video["qualities"] == [720]
    assert _single_video["urls"] == ["https://example.com/video"]


# --------------------------------------------------------------------------- #
# 2. CLI validation: invalid values
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("argclause,expect_usage", [
    (["--quality", "abc"], "--quality must be a positive integer"),
    (["--quality", "1.5"], "--quality must be a positive integer"),
    (["--quality", "0"], "--quality must be a positive integer"),
    (["--quality=-1"], "--quality must be a positive integer"),
    (["--quality", ""], "--quality must be a positive integer"),
])
def test_quality_invalid_value_rejected(monkeypatch, capsys, _single_video, argclause, expect_usage):
    argv = ["downv", *argclause, "https://example.com/video"]
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "Error:" in out
    assert _single_video["urls"] == []


def test_quality_missing_value_rejected(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "--quality"])
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "Error: --quality requires a height value" in out


def test_quality_equals_empty_rejected(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "--quality=", "https://example.com/video"])
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "Error: --quality requires a height value" in out


# --------------------------------------------------------------------------- #
# 3. Interactive compatibility: no --quality -> interactive selector still used
# --------------------------------------------------------------------------- #


def test_no_quality_uses_interactive_selector(monkeypatch, capsys, _single_video):
    menu_calls = {"n": 0}

    def fake_select_quality(q):
        menu_calls["n"] += 1
        return q[480]

    monkeypatch.setattr(cli, "select_quality", fake_select_quality)
    monkeypatch.setattr(sys, "argv", ["downv", "https://example.com/video"])
    assert cli.main() == 0
    # The interactive selector was invoked exactly once and picked 480p.
    assert menu_calls["n"] == 1
    assert _single_video["qualities"] == [480]


def test_quality_skips_interactive_selector(monkeypatch, capsys, _single_video):
    menu_calls = {"n": 0}

    def fake_select_quality(q):
        menu_calls["n"] += 1
        return q[480]

    monkeypatch.setattr(cli, "select_quality", fake_select_quality)
    monkeypatch.setattr(sys, "argv", ["downv", "--quality", "1080", "https://example.com/video"])
    assert cli.main() == 0
    # Non-interactive: the selector menu is never invoked.
    assert menu_calls["n"] == 0
    assert _single_video["qualities"] == [1080]


def test_download_video_interactive_when_no_quality(monkeypatch, capsys):
    """_download_video without quality shows the menu; with quality it does not."""
    info = _info([_fmt("720", 720), _fmt("480", 480)])
    menu_calls = {"n": 0}
    monkeypatch.setattr(cli, "select_formats", lambda i: {720: _selected(720), 480: _selected(480)})
    monkeypatch.setattr(cli, "select_quality", lambda q: menu_calls.__setitem__("n", menu_calls["n"] + 1) or q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "download_media", lambda i, s, o: __import__("pathlib").Path("x"))

    assert cli._download_video(info) == "downloaded"
    assert menu_calls["n"] == 1


def test_download_video_quality_picks_without_menu(monkeypatch, capsys):
    info = _info([_fmt("720", 720), _fmt("480", 480)])
    menu_calls = {"n": 0}
    picnic = []
    monkeypatch.setattr(cli, "select_formats", lambda i: {720: _selected(720), 480: _selected(480)})
    monkeypatch.setattr(cli, "select_quality", lambda q: menu_calls.__setitem__("n", menu_calls["n"] + 1) or q[480])
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)

    def fake_download(info, selected, output_dir):
        picnic.append(selected.height)
        return __import__("pathlib").Path("x")

    monkeypatch.setattr(cli, "download_media", fake_download)

    assert cli._download_video(info, quality=720) == "downloaded"
    assert menu_calls["n"] == 0
    assert picnic == [720]


# --------------------------------------------------------------------------- #
# 4. Format selection: pick_quality_by_height
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _qualities():
    return {
        2160: _selected(2160),
        1440: _selected(1440),
        1080: _selected(1080),
        720: _selected(720),
        480: _selected(480),
        360: _selected(360),
    }


def test_pick_exact_match(_qualities):
    assert pick_quality_by_height(_qualities, 1080).height == 1080


def test_pick_exact_match_720(_qualities):
    assert pick_quality_by_height(_qualities, 720).height == 720


def test_pick_falls_back_to_next_lower(_qualities):
    # 1080 unavailable -> closest at or below is 1080? No: exact absent, so 720.
    avail = {h: _qualities[h] for h in (2160, 1440, 720, 480)}
    assert pick_quality_by_height(avail, 1080).height == 720


def test_pick_does_not_select_higher_than_requested(_qualities):
    avail = {h: _qualities[h] for h in (2160, 1440, 720, 480)}
    # 1080 requested: 1080 absent, so fall back to 720 (not 1440/2160).
    assert pick_quality_by_height(avail, 1080).height == 720


def test_pick_when_requested_above_max_uses_max(_qualities):
    avail = {h: _qualities[h] for h in (720, 480, 360)}
    assert pick_quality_by_height(avail, 1080).height == 720


def test_pick_when_requested_below_min_uses_min(_qualities):
    avail = {h: _qualities[h] for h in (2160, 1440, 1080)}
    assert pick_quality_by_height(avail, 360).height == 1080


def test_pick_empty_returns_none():
    assert pick_quality_by_height({}, 1080) is None


def test_select_formats_still_builds_all_heights():
    info = _info([_fmt("2160", 2160), _fmt("1080", 1080), _fmt("720", 720), _fmt("480", 480)])
    result = select_formats(info)
    assert set(result) == {2160, 1080, 720, 480}


# --------------------------------------------------------------------------- #
# 5. Playlist: --quality does not show menu and applies consistently
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _playlist_pipeline(monkeypatch, tmp_path):
    seen = {"menu_calls": 0, "heights": []}

    def fake_get_media_info(url):
        return {
            "_type": "playlist",
            "title": "P",
            "playlist_count": 2,
            "entries": [
                {"id": "v1", "title": "One", "webpage_url": "https://e/v1"},
                {"id": "v2", "title": "Two", "webpage_url": "https://e/v2"},
            ],
        }

    monkeypatch.setattr(cli, "get_media_info", fake_get_media_info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {720: _selected(720), 480: _selected(480)})
    monkeypatch.setattr(cli, "select_quality", lambda q: seen.__setitem__("menu_calls", seen["menu_calls"] + 1) or q[480])
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: {**e, "formats": []})
    monkeypatch.setattr(cli, "find_existing_download", lambda i: None)
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fake_download(info, selected, output_dir):
        seen["heights"].append(selected.height)
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{info['id']}.mp4"
        out.write_bytes(b"d")
        return out

    monkeypatch.setattr(cli, "download_media", fake_download)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    return seen


def test_playlist_with_quality_skips_menu_and_applies(monkeypatch, capsys, _playlist_pipeline):
    monkeypatch.setattr(sys, "argv", ["downv", "--quality", "720", "https://example.com/pl"])
    assert cli.main() == 0
    assert _playlist_pipeline["menu_calls"] == 0
    # Both items downloaded at the requested 720p quality.
    assert _playlist_pipeline["heights"] == [720, 720]


def test_playlist_no_quality_still_uses_menu(monkeypatch, capsys, _playlist_pipeline):
    monkeypatch.setattr(sys, "argv", ["downv", "https://example.com/pl"])
    assert cli.main() == 0
    # Interactive playlist uses the aggregate menu once.
    assert _playlist_pipeline["menu_calls"] == 1
    assert _playlist_pipeline["heights"] == [480, 480]
