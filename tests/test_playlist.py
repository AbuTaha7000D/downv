"""Unit tests for Phase 6A playlist detection & confirmation."""

import sys

import pytest

from downv import cli


def _playlist_info(title="My Playlist", uploader="Example Channel", count=12, entries=None):
    info = {"_type": "playlist", "title": title, "entries": entries if entries is not None else [{}] * count}
    if uploader:
        info["uploader"] = uploader
    return info


def test_safe_playlist_count_prefers_playlist_count():
    info = {"playlist_count": 7, "entries": [{}] * 3}
    assert cli._safe_playlist_count(info) == 7


def test_safe_playlist_count_falls_back_to_len():
    info = {"entries": [{}] * 5}
    assert cli._safe_playlist_count(info) == 5


def test_safe_playlist_count_lazy_entries_returns_none(monkeypatch):
    class _Lazy:
        def __iter__(self):
            return iter([{}, {}])

        def __len__(self):
            raise TypeError("no len")

    info = {"entries": _Lazy()}
    assert cli._safe_playlist_count(info) is None


def test_safe_playlist_count_missing_returns_none():
    assert cli._safe_playlist_count({}) is None


def test_describe_playlist_uses_available_metadata():
    info = _playlist_info(title="My Playlist", uploader="Example Channel", count=12)
    title, uploader, count = cli._describe_playlist(info)
    assert title == "My Playlist"
    assert uploader == "Example Channel"
    assert count == 12


def test_describe_playlist_partial_metadata_does_not_crash():
    info = {"_type": "playlist"}
    title, uploader, count = cli._describe_playlist(info)
    assert title == "Untitled playlist"
    assert uploader == "Unknown"
    assert count is None


def test_handle_playlist_confirmed(monkeypatch, capsys):
    info = _playlist_info(count=12)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "y")
    assert cli._handle_playlist(info) is True
    out = capsys.readouterr().out
    assert "Playlist detected" in out
    assert "Title    : My Playlist" in out
    assert "Uploader : Example Channel" in out
    assert "Videos   : 12" in out
    assert prompts and "Download all 12 videos? [y/N]:" in prompts[0]


def test_handle_playlist_declined(monkeypatch, capsys):
    info = _playlist_info(count=12)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    assert cli._handle_playlist(info) is False
    assert prompts and "Download all 12 videos? [y/N]:" in prompts[0]
    out = capsys.readouterr().out
    assert "Playlist detected" in out


def test_handle_playlist_declined_default_empty(monkeypatch, capsys):
    info = _playlist_info(count=12)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert cli._handle_playlist(info) is False


def test_handle_playlist_unknown_count_uses_generic_prompt(monkeypatch, capsys):
    info = {"_type": "playlist", "title": "No Count", "entries": object()}
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    cli._handle_playlist(info)
    out = capsys.readouterr().out
    assert "Videos   : ?" in out
    assert prompts and "Download all videos? [y/N]:" in prompts[0]


def test_run_download_playlist_confirmed_no_media_download(monkeypatch, capsys):
    """Confirmation must not trigger any download/show single-video path."""
    info = _playlist_info(title="My Playlist", uploader="Example Channel", count=12)
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "download_media", lambda *a, **k: pytest.fail("download called"))
    monkeypatch.setattr(cli, "find_existing_download", lambda info: None)
    monkeypatch.setattr(cli, "select_formats", lambda info: {})
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert "Playlist detected" in out
    assert "My Playlist" in out
    assert "Videos   : 12" in out
    assert "✓ Playlist confirmed" in out


def test_run_download_playlist_declined_no_download(monkeypatch, capsys):
    info = _playlist_info(title="My Playlist", count=12)
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "download_media", lambda *a, **k: pytest.fail("download called"))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert "Playlist detected" in out
    assert "Download cancelled." in out


def test_run_download_empty_playlist_is_reported(monkeypatch, capsys):
    """An empty / no-usable-entries playlist is safely reported without crashing."""
    info = _playlist_info(title="Empty List", uploader="Channel", count=0, entries=[])
    monkeypatch.setattr(cli, "get_media_info", lambda url: info)
    monkeypatch.setattr(cli, "download_media", lambda *a, **k: pytest.fail("download called"))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(sys, "argv", ["downv"])

    cli._run_download()
    out = capsys.readouterr().out
    assert "Playlist detected" in out
    assert "Empty List" in out
    assert "Videos   : 0" in out
