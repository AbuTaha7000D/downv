"""CLI entry point for DownV."""

import sys
import termios
import tty
from pathlib import Path
from typing import Dict

from downv import history
from downv.downloader import (
    DownloadFailure,
    download_media,
    ffmpeg_available,
    find_existing_download,
)
from downv.extractor import MediaInfoError, get_media_info
from downv.formats import (
    SelectedMediaFormat,
    format_size,
    select_formats,
)
from downv.paths import OutputDirectoryError, get_output_directory


def prompt_for_url() -> str:
    while True:
        url = input("Enter URL: ").strip()
        if url:
            return url
        print()
        print("Please enter a URL.")
        print()


def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def display_info(info: dict) -> None:
    print("✓ Media information retrieved")
    print()

    title = info.get("title", "Unknown")
    uploader = info.get("uploader") or info.get("channel") or "Unknown"
    duration = info.get("duration")
    duration_str = format_duration(duration) if duration else "Unknown"

    print(f"Title    : {title}")
    print(f"Uploader : {uploader}")
    print(f"Duration : {duration_str}")

    chapters = info.get("chapters") or []
    if chapters:
        print(f"Chapters : {len(chapters)} chapters detected")
    else:
        print("Chapters : None")


def read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "UP"
            if seq == "[B":
                return "DOWN"
            if seq == "[C":
                return "RIGHT"
            if seq == "[D":
                return "LEFT"
            return "ESC"
        if ch in ("\r", "\n"):
            return "ENTER"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select_quality(qualities: Dict[int, SelectedMediaFormat]) -> SelectedMediaFormat:
    items = list(qualities.items())
    index = 0
    rendered_lines = 0

    def render() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            moves = rendered_lines - 1
            sys.stdout.write(f"\033[{moves}A")
            for i in range(rendered_lines):
                sys.stdout.write("\033[2K")
                if i < rendered_lines - 1:
                    sys.stdout.write("\n")
            sys.stdout.write(f"\033[{moves}A")
        lines = ["Available qualities:", ""]
        for i, (height, entry) in enumerate(items):
            size = format_size(entry.size_bytes)
            marker = "❯" if i == index else " "
            lines.append(f"{marker} {entry.label} — {size}")
        lines.append("")
        lines.append("Use ↑/↓ to navigate, Enter to select.")
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()
        rendered_lines = len(lines)

    while True:
        render()
        key = read_key()
        if key == "UP":
            index = (index - 1) % len(items)
        elif key == "DOWN":
            index = (index + 1) % len(items)
        elif key == "ENTER":
            selected = items[index][1]
            print()
            print(f"Selected: {selected.label}")
            return selected


def clear_history() -> None:
    """Clear all download history metadata (never touches media files)."""
    print("DownV - Download History")
    print()
    try:
        # Read through the API first so a corrupted history file is detected
        # rather than silently overwritten/truncated by the clear operation.
        history.get_download_history()
        history.clear_history()
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    print("✓ Download history cleared.")


def show_history_count() -> None:
    """Print the number of recorded downloads (metadata only, read-only)."""
    print("DownV - Download History")
    print()
    try:
        count = history.count_history()
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    print(f"Total downloads: {count}")


def remove_history(video_id: str) -> None:
    """Remove the history metadata record for a video (never the media file)."""
    print("DownV - Download History")
    print()
    try:
        removed = history.remove_download(video_id)
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    if removed is None:
        print(f"No record found for video ID: {video_id}")
        return

    print(f"✓ Removed download record for video ID: {video_id}")


def _file_status(record: dict) -> str:
    """Return a read-only status for the record's media file (existence only)."""
    filepath = record.get("filepath")
    if not filepath:
        return "✗ File missing"
    try:
        exists = Path(filepath).is_file()
    except (OSError, ValueError, TypeError):
        return "✗ File missing"
    return "✓ File exists" if exists else "✗ File missing"


def _print_existing(info: dict) -> None:
    """Print the most recent matching history record for an already-downloaded video."""
    print("✓ Video already downloaded")
    video_id = info.get("id")
    if not video_id:
        return
    try:
        records = history.find_downloads(video_id)
    except history.HistoryError as exc:
        print()
        print(f"Warning: {exc}")
        return
    if not records:
        return
    record = records[-1]
    title = record.get("title", "Unknown")
    quality = record.get("quality")
    quality_str = f"{quality}p" if quality else "n/a"
    filepath = record.get("filepath", "?")
    print(f"  Title    : {title}")
    print(f"  Quality  : {quality_str}")
    print(f"  File     : {filepath}")
    print(f"  Status   : {_file_status(record)}")


def show_history_detail(video_id: str) -> None:
    """Print the full metadata for a single recorded download."""
    print("DownV - Download History")
    print()
    try:
        record = history.find_download(video_id)
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    if record is None:
        print(f"No record found for video ID: {video_id}")
        return

    title = record.get("title", "Unknown")
    quality = record.get("quality")
    quality_str = f"{quality}p" if quality else "n/a"

    print(f"Title       : {title}")
    print(f"Video ID    : {record.get('video_id', '?')}")
    print(f"Quality     : {quality_str}")
    duration = record.get("duration")
    print(f"Duration    : {format_duration(duration) if duration else 'Unknown'}")
    print(f"Filename    : {record.get('filename', '?')}")
    print(f"Filepath    : {record.get('filepath', '?')}")
    print(f"Status      : {_file_status(record)}")
    print(f"Downloaded  : {record.get('downloaded_at', 'unknown')}")


def search_history(query: str) -> None:
    """Print history records matching ``query`` (title or video_id), newest first."""
    print("DownV - Download History")
    print()
    try:
        records = history.get_download_history()
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    needle = query.strip().lower()
    matches = [
        record
        for record in records
        if needle in (record.get("title") or "").lower()
        or needle in (record.get("video_id") or "").lower()
    ]

    if not matches:
        print("No matching downloads.")
        return

    for record in matches:
        title = record.get("title", "Unknown")
        video_id = record.get("video_id", "?")
        quality = record.get("quality")
        quality_str = f"{quality}p" if quality else "n/a"
        downloaded_at = record.get("downloaded_at", "unknown")
        print(f"- {title}")
        print(f"  Video ID   : {video_id}")
        print(f"  Quality    : {quality_str}")
        print(f"  Status     : {_file_status(record)}")
        print(f"  Downloaded : {downloaded_at}")

    print()
    print(f"Total: {len(matches)}")


def show_history() -> None:
    """Print the download history (metadata only), newest first."""
    print("DownV - Download History")
    print()
    try:
        records = history.get_download_history()
    except history.HistoryError as exc:
        print("✗ Could not read download history.")
        print()
        print(f"Reason: {exc}")
        return

    if not records:
        print("No downloads recorded yet.")
        return

    for record in records:
        title = record.get("title", "Unknown")
        video_id = record.get("video_id", "?")
        quality = record.get("quality")
        quality_str = f"{quality}p" if quality else "n/a"
        downloaded_at = record.get("downloaded_at", "unknown")
        print(f"- {title}")
        print(f"  Video ID   : {video_id}")
        print(f"  Quality    : {quality_str}")
        print(f"  Status     : {_file_status(record)}")
        print(f"  Downloaded : {downloaded_at}")

    print()
    print(f"Total: {len(records)}")


def _run_download() -> None:
    print("DownV - Media Downloader")
    print()
    url = prompt_for_url()
    print()
    print("Fetching media information...")

    try:
        info = get_media_info(url)
    except MediaInfoError as exc:
        print("✗ Failed to retrieve media information.")
        print()
        print(f"Reason: {exc}")
        return

    if "entries" in info:
        print("Playlist detected.")
        print()
        playlist_length = len(info.get("entries") or [])
        print(f"Title    : {info.get('title', 'Unknown')}")
        print(f"Uploader : {info.get('uploader') or info.get('channel') or 'Unknown'}")
        print(f"Items    : {playlist_length}")
        return

    display_info(info)

    qualities = select_formats(info)
    if not qualities:
        print("No video formats available.")
        return

    print()
    selected = select_quality(qualities)

    output_dir = Path.home() / "Videos" / "downv"
    was_missing = not output_dir.exists()
    try:
        output_dir = get_output_directory()
    except OutputDirectoryError as exc:
        print("✗ Could not create output directory.")
        print()
        print(f"Reason: {exc}")
        return

    if was_missing:
        print(f"✓ Created output directory: {output_dir}/")
    else:
        print(f"✓ Output directory ready: {output_dir}/")

    existing = find_existing_download(info)
    if existing:
        print()
        _print_existing(info)
        return

    if selected.needs_merge and not ffmpeg_available():
        print()
        print("✗ FFmpeg is required to download this quality.")
        print()
        print("Please install FFmpeg and try again.")
        return

    print()
    print("Starting download...")
    print()
    try:
        download_media(info, selected, output_dir)
    except DownloadFailure as exc:
        print("✗ Download failed.")
        print()
        print(f"Reason: {exc}")
        return

    print()
    print("✓ Download completed")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "history":
        if len(args) >= 2 and args[1] == "remove":
            if len(args) >= 3:
                remove_history(args[2])
            else:
                print("Usage: downv history remove <video_id>")
            return
        if len(args) >= 2 and args[1] == "clear":
            if len(args) >= 3:
                print("Usage: downv history clear")
            else:
                clear_history()
            return
        if len(args) >= 2 and args[1] == "search":
            if len(args) >= 3:
                search_history(args[2])
            else:
                print("Usage: downv history search <query>")
            return
        if len(args) >= 2 and args[1] == "count":
            if len(args) >= 3:
                print("Usage: downv history count")
            else:
                show_history_count()
            return
        if len(args) >= 2:
            show_history_detail(args[1])
        else:
            show_history()
        return
    if args:
        print(f"Unknown command: {args[0]}")
        print()
        print("Usage:")
        print("  downv                       Download a video interactively")
        print("  downv history               Show download history")
        print("  downv history <id>          Show details for one video")
        print("  downv history remove <id>   Remove a history record (metadata only)")
        print("  downv history clear         Clear all history records (metadata only)")
        print("  downv history search <q>    Search history by title or video ID")
        print("  downv history count         Show number of recorded downloads")
        return
    _run_download()


if __name__ == "__main__":
    main()
