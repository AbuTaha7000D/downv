# DownV

DownV is a modern, interactive command-line media downloader built around
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and FFmpeg. It downloads single
videos and playlists from the many sites yt-dlp supports, guides you through
choosing a quality when you want a hand, and lets you pin an exact quality
non-interactively when you do not. You can also grab just the audio of any
video as an MP3 with `--audio`.

> **Status:** DownV is under active development. Features are added
> incrementally milestone by milestone.

## Features

- **Interactive quality selection** — browse available qualities and sizes with
  an arrow-key menu.
- **Non-interactive quality selection** — pick a quality on the command line
  with `--quality`.
- **Audio-only downloads** — extract the best audio stream as an MP3 with
  `--audio`.
- **Single-video and playlist downloads** — the same download pipeline is used
  for both.
- **Video + audio format handling** — separate video and audio streams are
  merged into a single file when needed.
- **Accurate size estimation** — shows an estimated file size before you commit.
- **Duplicate detection** — already-downloaded videos are recognised and
  skipped instead of re-downloaded.
- **Collision-safe filenames** — files are never overwritten; name collisions
  get a numeric suffix.
- **Download history** — a persistent, inspectable record of your downloads.
- **Optional chapter embedding** — embeds detected chapters into the file using
  FFmpeg metadata.
- **Configurable output directory** — via `--output`, the `DOWNV_OUTPUT_DIR`
  environment variable, or the default.
- **Verbose diagnostics** — `-v` / `--verbose` prints details about what DownV
  is doing.
- **Graceful input handling** — clean cancel on `Ctrl+C` / `Ctrl+D`, and retry
  support for failed playlist items.

## Requirements

- **Python** >= 3.10
- **yt-dlp** (installed automatically as a Python dependency)
- **FFmpeg** — required on the system `PATH` when the chosen quality needs
  video and audio streams merged, and always required for `--audio` downloads
  (which transcode the audio stream to MP3).

## Installation

```bash
git clone https://github.com/AbuTaha7000D/downv.git
cd downv
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Verify the install:

```bash
downv --version
```

You can also run it without installing, via the module:

```bash
python -m downv
```

## Usage

With no arguments, DownV starts interactively and prompts for a URL:

```bash
downv
```

A URL may also be passed directly as a positional argument:

```bash
downv "https://www.youtube.com/watch?v=..."
```

### Interactive mode

In interactive mode (no `--audio` or `--quality`), DownV first asks what kind of
media to download:

```text
Select media type:

  ❯ Video
    Audio
```

Use `↑`/`↓` and `Enter` to pick. Selecting **Video** continues to the quality
selection; selecting **Audio** downloads the best audio stream directly (see
[Audio-only downloads](#audio-only-downloads)).

Without `--quality`, a video download fetches the media's available formats and
shows an arrow-key quality menu:

```text
Available qualities:

  ❯ 1080p — ~250.0 MB
    720p  — ~180.0 MB
    480p  — ~100.0 MB
```

Use `↑`/`↓` to navigate and `Enter` to select.

### Non-interactive quality selection

Pass `--quality HEIGHT` to skip the menu and select a quality automatically:

```bash
downv --quality 1080 "https://www.youtube.com/watch?v=..."
```

The `=` form works too:

```bash
downv --quality=720 "https://www.youtube.com/watch?v=..."
```

`HEIGHT` is the requested, preferred video height in pixels. Selection works as
follows:

- the exact requested height is used when available;
- if the exact height is unavailable, the closest available height **at or
  below** the request is selected (e.g. `--quality 1080` with only 720p
  available selects 720p);
- if the requested height is higher than every available option, the highest
  available quality is selected;
- if the requested height is lower than every available option, the lowest
  available quality is selected.

`--quality` never presents the interactive menu.

### Playlist downloads

```bash
downv "https://www.youtube.com/playlist?list=..."
```

DownV detects the playlist, displays its title, uploader and video count, and
asks for confirmation before downloading. Items are saved into a dedicated
playlist subdirectory. When `--quality` is supplied, the same quality is
applied to every item (with the per-item fallback behaviour described above).
After finishing, DownV prints a summary of the results:

```text
Playlist complete

  Total      : 12
  Downloaded : 10
  Skipped    : 1
  Failed     : 0
  Unresolved : 1
```

Failed or unresolved items can be retried from the prompt that follows.

### Audio-only downloads

Pass `--audio` to download only the audio of a video (or playlist), extracted
to an MP3 using the best available audio stream:

```bash
downv --audio "https://www.youtube.com/watch?v=..."
```

`--audio`:
- selects audio-only mode immediately, skipping the interactive Video/Audio
  menu and all quality and chapter prompts;
- works for single videos and playlists (every item becomes an MP3);
- requires FFmpeg to extract and transcode the audio;
- cannot be combined with `--quality` (which is a video-height selector).

Audio and video downloads of the same media are tracked separately, so
downloading a video then its audio is never mistaken for a duplicate.

### Custom output directory

Force a specific location with `--output`:

```bash
downv --output ~/Downloads "https://www.youtube.com/watch?v=..."
```

The options may appear before or after the URL:

```bash
downv --quality 720 --output ~/Videos/test "https://www.youtube.com/watch?v=..."
```

### Verbose diagnostics

Enable detailed diagnostics to see what DownV is doing under the hood:

```bash
downv --verbose "https://www.youtube.com/watch?v=..."
```

The short form `-v` is equivalent. Verbose mode prints `[DEBUG]` lines covering
the URL source, media type, chosen quality, and output directory, and includes
extra detail on download errors and playlist retries.

### Chapters

When a video has detectable chapters, DownV asks whether to embed them into the
downloaded file:

```text
Download chapters? [y/N]:
```

Answering `y` runs FFmpeg after the download to write the chapter markers into
the file's metadata. For playlists, DownV asks once whether to embed chapters
for any videos that have them, and applies that choice to every item.

## Command-line Options

| Option | Description |
|--------|-------------|
| `-h`, `--help` | Show help and exit |
| `-V`, `--version` | Show version and exit |
| `-v`, `--verbose` | Enable verbose `[DEBUG]` diagnostics |
| `--output DIR` | Save downloads into `DIR` |
| `--output=DIR` | Save downloads into `DIR` (equivalent to `--output DIR`) |
| `--quality HEIGHT` | Download at the specified height (e.g. 1080, 720); skips the interactive menu |
| `--quality=HEIGHT` | Equivalent to `--quality HEIGHT` |
| `--audio` | Download audio only (MP3); cannot be combined with `--quality` |

### History subcommands

Download history can be inspected from the command line:

| Command | Description |
|---------|-------------|
| `downv history` | Show the download history |
| `downv history count` | Show the number of recorded downloads |
| `downv history search <query>` | Search recorded downloads |
| `downv history detail <id>` | Show details for a recorded video |
| `downv history remove <id>` | Remove a recorded video's history by ID |
| `downv history clear` | Clear the download history |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success / normal cancellation |
| `1` | Error / invalid usage |
| `130` | Interrupted with `Ctrl+C` |

## Output Location

Downloads are saved to the first directory found in this order:

```text
--output
    ↓
DOWNV_OUTPUT_DIR
    ↓
default output directory
```

The default output directory is:

```text
~/Videos/downv
```

Set the environment variable to redirect downloads without a flag:

```bash
export DOWNV_OUTPUT_DIR="$HOME/Downloads"
```

Playlist items are saved into a subdirectory named after the playlist, under
the resolved output directory.

## Duplicate Detection & History

After a download completes, DownV records the video's identity and the resulting
file's location and size in a persistent JSON history. When the same video is
requested again, DownV checks this history first: if a matching file still
exists and is unchanged, the download is skipped as an "already downloaded"
video rather than fetched again.

- Duplicate detection is based on the video's identity and the recorded file
  information (size and modification time).
- Filename collisions with unrelated files are handled safely by adding a
  numeric suffix, never by overwriting.
- History commands only touch metadata — **media files are never deleted** by
  history operations.

The history file lives at:

```text
~/.local/share/downv/history.json
```

## Development

### Running Tests

The suite uses `pytest`. From the project root:

```bash
pytest
```

The current suite contains **366 tests** covering the CLI, formats, playlists,
history, chapters, audio-only downloads, and error handling.

### Project Structure

```text
downv/
├── cli.py          # CLI entry point, argument parsing, interactive menus
├── downloader.py   # yt-dlp download engine, duplicate detection, history
├── extractor.py    # metadata-only extraction via yt-dlp
├── formats.py      # format analysis and quality selection
├── history.py      # persistent download history (JSON read/write)
└── paths.py        # output and data directory resolution
```

## Roadmap

DownV is developed in phases. The current release includes all milestones completed through:

- **9.2 — Non-interactive quality selection** (`--quality`)
- **9.4 — Minimal GitHub Actions CI**
- **10.1 — Audio-only mode** (`--audio`)

Planned next:

- **Phase 10 — Subtitles, profiles** (remaining parts of Phase 10)

These are direction, not commitments; features land as they are implemented.

## License

[MIT](LICENSE)
