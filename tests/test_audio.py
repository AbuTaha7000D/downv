"""Tests for Phase 10.1 audio-only download mode (``--audio``).

Covers CLI parsing/validation of ``--audio`` (including the ``--audio`` +
``--quality`` conflict), the interactive Video/Audio media-type selector,
audio format selection, audio download/FFmpeg/history behaviour, and audio
playlists. It also locks in that existing interactive video behaviour without
``--audio`` remains unchanged.
"""

import sys

import pytest

from downv import cli, downloader, history
from downv.formats import SelectedAudio, SelectedMediaFormat, select_best_audio


def _selected(height=480):
    return SelectedMediaFormat(height, str(height), None, 1000)


def _audio_fmt(fmt_id, abr=128, size=1000):
    return {
        "format_id": fmt_id,
        "abr": abr,
        "acodec": "mp4a",
        "vcodec": "none",
        "filesize": size,
    }


def _video_fmt(fmt_id, height):
    return {
        "format_id": fmt_id,
        "height": height,
        "abr": None,
        "acodec": "none",
        "vcodec": "avc1",
        "filesize": 500,
    }


def _info(video_id="v1", title="T", audio_fmts=None, video_fmts=None):
    return {
        "_type": "video",
        "id": video_id,
        "title": title,
        "webpage_url": f"https://example.com/watch?v={video_id}",
        "formats": (audio_fmts or []) + (video_fmts or []),
    }


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("DOWNV_OUTPUT_DIR", raising=False)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "get_data_directory", lambda: tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. formats.select_best_audio
# --------------------------------------------------------------------------- #


def test_select_best_audio_picks_highest_bitrate():
    info = _info(
        audio_fmts=[_audio_fmt("a1", abr=64), _audio_fmt("a2", abr=192), _audio_fmt("a3", abr=128)]
    )
    best = select_best_audio(info)
    assert best.audio_fmt_id == "a2"
    assert best.size_bytes == 1000


def test_select_best_audio_ignores_video_formats():
    info = _info(audio_fmts=[_audio_fmt("a1", abr=128)], video_fmts=[_video_fmt("v1", 720)])
    best = select_best_audio(info)
    assert best.audio_fmt_id == "a1"


def test_select_best_audio_none_when_no_audio():
    info = _info(video_fmts=[_video_fmt("v1", 720)])
    assert select_best_audio(info) is None


def test_selected_audio_format_selector_is_audio_id():
    sel = SelectedAudio("a1", 1000)
    assert sel.format_selector == "a1"


# --------------------------------------------------------------------------- #
# 2. CLI parsing: --audio
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _single_video(monkeypatch, tmp_path):
    """Mock a standalone single-video download, recording URL and media type."""
    calls = {"urls": [], "media_types": []}

    def fake_get_media_info(url):
        calls["urls"].append(url)
        return _info(audio_fmts=[_audio_fmt("a1")], video_fmts=[_video_fmt("v1", 720)])

    monkeypatch.setattr(cli, "get_media_info", fake_get_media_info)
    monkeypatch.setattr(cli, "select_formats", lambda i: {720: _selected(720)})
    monkeypatch.setattr(cli, "select_quality", lambda q: q[720])
    monkeypatch.setattr(cli, "find_existing_download", lambda i, media_type="video": None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fake_download_audio(info, selected, output_dir):
        calls["media_types"].append("audio")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "x.mp3").write_bytes(b"x")
        return output_dir / "x.mp3"

    def fake_download_media(info, selected, output_dir, preserve_chapters=False):
        calls["media_types"].append("video")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "x.mp4").write_bytes(b"x")
        return output_dir / "x.mp4"

    monkeypatch.setattr(cli, "download_audio", fake_download_audio)
    monkeypatch.setattr(cli, "download_media", fake_download_media)
    return calls


def test_audio_flag_routes_to_audio(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "--audio", "https://example.com/video"])
    assert cli.main() == 0
    assert _single_video["urls"] == ["https://example.com/video"]
    assert _single_video["media_types"] == ["audio"]


def test_audio_flag_after_url(monkeypatch, capsys, _single_video):
    monkeypatch.setattr(sys, "argv", ["downv", "https://example.com/video", "--audio"])
    assert cli.main() == 0
    assert _single_video["media_types"] == ["audio"]


def test_audio_skips_interactive_media_menu(monkeypatch, capsys, _single_video):
    menu = {"n": 0}
    monkeypatch.setattr(cli, "select_media_type", lambda: menu.__setitem__("n", menu["n"] + 1) or "video")
    monkeypatch.setattr(sys, "argv", ["downv", "--audio", "https://example.com/video"])
    assert cli.main() == 0
    assert menu["n"] == 0
    assert _single_video["media_types"] == ["audio"]


def test_no_flags_defaults_to_video_interactive(monkeypatch, capsys, _single_video):
    """Without --audio/--quality the interactive media menu is shown; non-TTY
    defaults to video so existing video behaviour is preserved."""
    menu = {"n": 0}
    monkeypatch.setattr(cli, "select_media_type", lambda: menu.__setitem__("n", menu["n"] + 1) or "video")
    monkeypatch.setattr(sys, "argv", ["downv", "https://example.com/video"])
    assert cli.main() == 0
    assert menu["n"] == 1
    assert _single_video["media_types"] == ["video"]


# --------------------------------------------------------------------------- #
# 3. CLI validation: --audio + --quality conflict
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("argv", [
    ["downv", "--audio", "--quality", "720", "https://example.com/video"],
    ["downv", "--quality", "720", "--audio", "https://example.com/video"],
    ["downv", "--audio", "--quality=720", "https://example.com/video"],
])
def test_audio_with_quality_rejected(monkeypatch, capsys, _single_video, argv):
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "Error: --audio cannot be combined with --quality" in out
    assert _single_video["urls"] == []


def test_audio_coexists_with_output(monkeypatch, tmp_path, capsys, _single_video):
    override = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "downv", "--audio", "--output", str(override), "https://example.com/video",
    ])
    assert cli.main() == 0
    assert _single_video["media_types"] == ["audio"]


# --------------------------------------------------------------------------- #
# 4. Interactive media-type selector
# --------------------------------------------------------------------------- #


def test_select_media_type_defaults_video_non_tty(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.select_media_type() == "video"


def test_select_media_type_interactive_navigation(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    keys = iter(["DOWN", "ENTER"])
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    assert cli.select_media_type() == "audio"


def test_select_media_type_interactive_video_default(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "read_key", lambda: "ENTER")
    assert cli.select_media_type() == "video"


def test_select_media_type_cancel_on_eof(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "read_key", lambda: "")
    assert cli.select_media_type() is None


# --------------------------------------------------------------------------- #
# 5. Downloader: download_audio
# --------------------------------------------------------------------------- #


def test_download_audio_requires_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "ffmpeg_available", lambda: False)
    info = _info(audio_fmts=[_audio_fmt("a1")])
    from downv.downloader import DownloadFailure
    with pytest.raises(DownloadFailure):
        downloader.download_audio(info, SelectedAudio("a1", 1000), tmp_path)


def test_download_audio_records_audio_history(monkeypatch, tmp_path, data_dir):
    monkeypatch.setattr(downloader, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(downloader, "find_existing_download", lambda info, media_type="video": None)
    from downv.downloader import download_audio
    from downv.formats import SelectedAudio

    class _FakeYDL:
        def __init__(self, options):
            self.url_audio = options["format"]
            self.postprocessors = options["postprocessors"]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            (tmp_path / "T.mp3").write_bytes(b"audio")

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _FakeYDL)
    result = download_audio(_info(audio_fmts=[_audio_fmt("a1")]), SelectedAudio("a1", 1000), tmp_path)
    assert result.name == "T.mp3"
    record = history.find_download("v1")
    assert record["media_type"] == "audio"
    assert record["quality"] is None
    assert record["filename"] == "T.mp3"


def test_download_audio_uses_mp3_postprocessor(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(downloader, "find_existing_download", lambda info, media_type="video": None)
    captured = {}

    class _FakeYDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            (tmp_path / "T.mp3").write_bytes(b"audio")

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _FakeYDL)
    downloader.download_audio(_info(audio_fmts=[_audio_fmt("a1")]), SelectedAudio("a1", 1000), tmp_path)
    assert captured["format"] == "a1"
    assert captured["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert captured["postprocessors"][0]["preferredcodec"] == "mp3"


# --------------------------------------------------------------------------- #
# 6. Duplicate detection separation (video vs audio)
# --------------------------------------------------------------------------- #


def test_audio_not_duplicate_of_video(monkeypatch, tmp_path, data_dir):
    """A video history record with a real file does not block an audio download."""
    video_file = tmp_path / "Title.mp4"
    video_file.write_bytes(b"v")
    history.record_download(
        video_id="v1", title="T", url="https://e/v1", filename="Title.mp4",
        filepath=str(video_file), quality=480, duration=60, file_size=1, media_type="video",
    )
    assert downloader.find_existing_download(_info(), media_type="video") is not None
    assert downloader.find_existing_download(_info(), media_type="audio") is None


def test_video_not_duplicate_of_audio(monkeypatch, tmp_path, data_dir):
    audio_file = tmp_path / "Title.mp3"
    audio_file.write_bytes(b"a")
    history.record_download(
        video_id="v1", title="T", url="https://e/v1", filename="Title.mp3",
        filepath=str(audio_file), quality=None, duration=60, file_size=1, media_type="audio",
    )
    assert downloader.find_existing_download(_info(), media_type="audio") is not None
    assert downloader.find_existing_download(_info(), media_type="video") is None


def test_legacy_record_without_media_type_treated_as_video(tmp_path, data_dir):
    """Records created before media_type existed are still treated as video."""
    video_file = tmp_path / "Title.mp4"
    video_file.write_bytes(b"v")
    history.record_download(
        video_id="v1", title="T", url="https://e/v1", filename="Title.mp4",
        filepath=str(video_file), quality=480, duration=60, file_size=1,
    )
    assert downloader.find_existing_download(_info(), media_type="video") is not None
    assert downloader.find_existing_download(_info(), media_type="audio") is None


# --------------------------------------------------------------------------- #
# 7. Audio playlist via --audio
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _audio_playlist_pipeline(monkeypatch, tmp_path):
    seen = {"menu_calls": 0, "audio": []}

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
    monkeypatch.setattr(cli, "select_quality", lambda q: seen.__setitem__("menu_calls", seen["menu_calls"] + 1) or q[480])
    monkeypatch.setattr(cli, "select_formats", lambda i: {480: _selected(480)})
    monkeypatch.setattr(cli, "_resolve_playlist_entry", lambda e: {**e, "formats": [_audio_fmt("a1")]})
    monkeypatch.setattr(cli, "find_existing_download", lambda i, media_type="video": None)
    monkeypatch.setattr(cli, "get_output_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fake_download_audio(info, selected, output_dir):
        seen["audio"].append(info["id"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{info['id']}.mp3").write_bytes(b"d")
        return output_dir / f"{info['id']}.mp3"

    monkeypatch.setattr(cli, "download_audio", fake_download_audio)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    return seen


def test_audio_playlist_skips_quality_menu(monkeypatch, capsys, _audio_playlist_pipeline):
    monkeypatch.setattr(sys, "argv", ["downv", "--audio", "https://example.com/pl"])
    assert cli.main() == 0
    assert _audio_playlist_pipeline["menu_calls"] == 0
    # Every item downloaded as audio into the playlist directory.
    assert _audio_playlist_pipeline["audio"] == ["v1", "v2"]


def test_process_playlist_media_type_audio_downloads_audio(monkeypatch, capsys, tmp_path):
    """_process_playlist with media_type='audio' routes every item to audio."""
    calls = {"audio": []}
    info = {
        "_type": "playlist",
        "title": "P",
        "entries": [
            {"id": "v1", "title": "One", "webpage_url": "https://e/v1", "formats": [_audio_fmt("a1")]},
            {"id": "v2", "title": "Two", "webpage_url": "https://e/v2", "formats": [_audio_fmt("a1")]},
        ],
    }
    monkeypatch.setattr(cli, "find_existing_download", lambda i, media_type="video": None)
    monkeypatch.setattr(cli, "ffmpeg_available", lambda: True)

    def fake_download_audio(info, selected, output_dir):
        calls["audio"].append(info["id"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{info['id']}.mp3").write_bytes(b"d")
        return output_dir / f"{info['id']}.mp3"

    monkeypatch.setattr(cli, "download_audio", fake_download_audio)

    stats = cli._process_playlist(info, playlist_dir=tmp_path, media_type="audio")
    assert calls["audio"] == ["v1", "v2"]
    assert stats["downloaded"] == 2
    assert stats["total"] == 2


# --------------------------------------------------------------------------- #
# 8. Single video audio chapters prompt skipped
# --------------------------------------------------------------------------- #


def test_single_audio_skips_chapter_prompt(monkeypatch, capsys, _single_video):
    """Audio mode must not ask about chapters."""
    info = _info(audio_fmts=[_audio_fmt("a1")], video_fmts=[_video_fmt("v1", 720)])
    info["chapters"] = [{"title": "Intro", "start_time": 0, "end_time": 5}]
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    prompts = []
    monkeypatch.setattr(cli, "_ask_preserve_chapters", lambda p: prompts.append(p) or True)
    monkeypatch.setattr(sys, "argv", ["downv", "--audio", "https://example.com/video"])
    assert cli.main() == 0
    assert _single_video["media_types"] == ["audio"]
    assert prompts == []


# --------------------------------------------------------------------------- #
# 9. Audio missing stream -> failed
# --------------------------------------------------------------------------- #


def test_download_audio_no_stream_returns_failed(monkeypatch, capsys):
    info = _info(video_fmts=[_video_fmt("v1", 720)])
    monkeypatch.setattr(cli, "select_best_audio", lambda i: None)
    assert cli._download_audio(info) == "failed"
    out = capsys.readouterr().out
    assert "No audio available" in out
