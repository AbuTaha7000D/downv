# DownV

DownV is a simple command-line media downloader built on [yt-dlp](https://github.com/yt-dlp/yt-dlp) and FFmpeg. It downloads videos and playlists, supports audio-only mode, lets you pick a quality, and keeps a history of what you've downloaded.

## Features

- Download single videos or whole playlists
- Audio-only downloads (`--audio`)
- Choose a quality interactively or with `--quality`
- Choose an output directory (`--output` or `DOWNV_OUTPUT_DIR`)
- Duplicate detection — already-downloaded media is skipped
- Download history (`downv history`)
- Optional chapter embedding
- Verbose diagnostics (`--verbose`)

## Requirements

- **Python** 3.10+
- **yt-dlp** (installed automatically as a dependency)
- **FFmpeg** — required when a download needs video/audio merging, and always for audio downloads

## Installation

```bash
git clone https://github.com/AbuTaha7000D/downv.git
cd downv
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Verify it works:

```bash
downv --version
```

## Usage

With no URL, DownV prompts for one. You can also pass a URL directly as an argument.

### 1. Download a video

```bash
downv "https://www.youtube.com/watch?v=..."
```

Without `--quality`, DownV lets you choose a quality from an interactive menu.

### 2. Download audio

```bash
downv --audio "https://www.youtube.com/watch?v=..."
```

Downloads just the audio of a video (or playlist) as an MP3. Requires FFmpeg and cannot be combined with `--quality`.

### 3. Choose a quality

```bash
downv --quality 720 "https://www.youtube.com/watch?v=..."
```

Skips the interactive menu and downloads at (or close to) the requested height. Valid heights include `480`, `720`, `1080`, and so on.

### 4. Download a playlist

```bash
downv "https://www.youtube.com/playlist?list=..."
```

DownV asks for confirmation, downloads each video into a playlist-named subfolder, and prints a summary. Add `--audio` to save every item as audio, or `--quality` to apply one quality to every video.

### 5. Choose an output directory

```bash
downv --output ~/Downloads "https://www.youtube.com/watch?v=..."
```

Downloads go to the first directory found in this order:

```text
--output
    ↓
DOWNV_OUTPUT_DIR
    ↓
default (~/Videos/downv)
```

You can also set the environment variable instead of a flag:

```bash
export DOWNV_OUTPUT_DIR="$HOME/Downloads"
```

### 6. Verbose mode

```bash
downv --verbose "https://www.youtube.com/watch?v=..."
```

Prints detailed diagnostics about what DownV is doing. The short form `-v` works too.

## Examples

```bash
# Download a video and choose quality interactively
downv "https://www.youtube.com/watch?v=abc123"

# Download at a fixed quality
downv --quality 1080 "https://www.youtube.com/watch?v=abc123"

# Download audio only
downv --audio "https://www.youtube.com/watch?v=abc123"

# Download an entire playlist at 720p
downv --quality 720 "https://www.youtube.com/playlist?list=xyz"

# Save everything to a specific folder
downv --output ~/Music "https://www.youtube.com/watch?v=abc123"
```

### Command-line options

| Option | Description |
|--------|-------------|
| `-h`, `--help` | Show help and exit |
| `-V`, `--version` | Show version and exit |
| `-v`, `--verbose` | Enable verbose diagnostics |
| `--output DIR` | Save downloads into `DIR` |
| `--quality HEIGHT` | Download at the requested height (e.g. `720`, `1080`) |
| `--audio` | Download audio only (MP3) |

### History

| Command | Description |
|---------|-------------|
| `downv history` | Show the download history |
| `downv history count` | Show how many downloads are recorded |
| `downv history search <query>` | Search recorded downloads |
| `downv history detail <id>` | Show details for a recorded video |
| `downv history remove <id>` | Remove a video's history record |
| `downv history clear` | Clear the download history |

## Development

```bash
git clone https://github.com/AbuTaha7000D/downv.git
cd downv
python -m venv .venv
source .venv/bin/activate
python -m pip install -e . pytest
pytest
```

## License

[MIT](LICENSE)
