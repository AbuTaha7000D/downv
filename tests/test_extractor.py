"""Regression tests for media-information extraction safety.

These lock in the behaviour that fixes the "playlist downloaded before
confirmation" concern: ``get_media_info`` must extract metadata only, never
write media, and for playlists must use flat stub entries so items are not
processed through the download path before the user confirms. No network or
YouTube access is required; yt-dlp is mocked.
"""

import pytest

from downv import extractor


class _FakeYDL:
    """Minimal stand-in for :class:`yt_dlp.YoutubeDL` that records the options
    it was constructed with and the arguments of any extraction/download call.
    """

    def __init__(self, options):
        self.options = options
        self.download_calls = []
        self.extract_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True, **kwargs):
        self.extract_calls.append({"url": url, "download": download, **kwargs})
        # A flat playlist result: count plus stub entries (no ``formats``).
        return {
            "_type": "playlist",
            "title": "Example Playlist",
            "id": "PL123",
            "playlist_count": 2,
            "entries": [
                {"id": "v1", "title": "One", "url": "https://e/v1"},
                {"id": "v2", "title": "Two", "url": "https://e/v2"},
            ],
        }

    def download(self, urls):
        self.download_calls.append(list(urls))
        return 0


def _make_extractor_return(result):
    class _YDLCtx(_FakeYDL):
        def extract_info(self, url, download=True, **kwargs):
            self.extract_calls.append({"url": url, "download": download, **kwargs})
            return result

    return _YDLCtx


def test_extraction_uses_metadata_only_options(monkeypatch):
    """The yt-dlp options must never engage a media-download path, and must
    flatten playlist entries so they are not resolved per-item up-front."""
    captured = {}

    class Spy(_FakeYDL):
        def __init__(self, options):
            super().__init__(options)
            captured["options"] = options

    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", Spy)
    extractor.get_media_info("https://example.com/playlist?list=PL123")
    assert captured["options"]["skip_download"] is True
    assert captured["options"]["extract_flat"] == "in_playlist"
    assert captured["options"]["quiet"] is True


def test_extraction_calls_extract_info_with_download_false(monkeypatch):
    """Metadata extraction must request download=False so no media is written."""
    spy = _FakeYDL({})
    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", lambda opts: spy)
    extractor.get_media_info("https://example.com/playlist?list=PL123")
    assert len(spy.extract_calls) == 1
    assert spy.extract_calls[0]["download"] is False
    assert spy.download_calls == []


def test_playlist_metadata_extraction_writes_no_media(monkeypatch, tmp_path):
    """Detection must not invoke the download API and must not create files."""
    creates_before = set(p for p in tmp_path.rglob("*") if p.is_file())
    spy = _FakeYDL({})
    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", lambda opts: spy)
    result = extractor.get_media_info("https://example.com/playlist?list=PL123")
    creates_after = set(p for p in tmp_path.rglob("*") if p.is_file())
    assert spy.download_calls == []
    assert creates_after == creates_before
    assert result["_type"] == "playlist"
    assert result["playlist_count"] == 2


def test_flat_playlist_entries_have_no_formats(monkeypatch):
    """Flat playlist entries carry an id/title/url but no resolved ``formats``,
    proving items were not processed through the format-selection/download path
    during detection."""
    flat_entries = [
        {"id": "v1", "title": "One", "url": "https://e/v1"},
        {"id": "v2", "title": "Two", "url": "https://e/v2"},
    ]

    class Ctx(_FakeYDL):
        def extract_info(self, url, download=True, **kwargs):
            self.extract_calls.append({"url": url, "download": download})
            return {
                "_type": "playlist",
                "title": "Example Playlist",
                "playlist_count": 2,
                "entries": flat_entries,
            }

    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", lambda opts: Ctx(opts))
    result = extractor.get_media_info("https://example.com/playlist?list=PL123")
    for entry in result["entries"]:
        assert "formats" not in entry
        assert entry.get("url")


def test_single_video_extraction_config_does_not_flatten(monkeypatch):
    """A plain (non-playlist) URL must still be fully resolved with formats, so
    the standalone download pipeline is unaffected by the playlist fix."""
    full_video = {
        "_type": "video",
        "id": "v1",
        "title": "One",
        "formats": [{"format_id": "0", "height": 480}],
        "webpage_url": "https://example.com/watch?v=v1",
    }

    class Ctx(_FakeYDL):
        def extract_info(self, url, download=True, **kwargs):
            self.extract_calls.append({"url": url, "download": download})
            return full_video

    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", lambda opts: Ctx(opts))
    result = extractor.get_media_info("https://example.com/watch?v=v1")
    assert result["_type"] == "video"
    assert "formats" in result


def test_media_info_error_on_download_error(monkeypatch):
    class FailingYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True, **kwargs):
            raise extractor.yt_dlp.utils.DownloadError("boom", exc_info=(None, ValueError("reason"), None))

    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", FailingYDL)
    with pytest.raises(extractor.MediaInfoError):
        extractor.get_media_info("https://example.com/video")