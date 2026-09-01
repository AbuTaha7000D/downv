"""Path resolution for DownV output locations."""

from pathlib import Path


class OutputDirectoryError(Exception):
    """Raised when the output directory cannot be created."""


class DataDirectoryError(Exception):
    """Raised when the DownV data directory cannot be created."""


def get_output_directory() -> Path:
    """Return the default output directory, creating it if necessary."""
    output_dir = Path.home() / "Videos" / "downv"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputDirectoryError(f"{exc.strerror or exc} ({exc.filename or output_dir})") from exc
    return output_dir


def get_data_directory() -> Path:
    """Return DownV's application-data directory, creating it if necessary.

    Uses the standard Linux user data location (~/.local/share/downv) resolved
    through ``Path.home()`` so it is never hard-coded to a specific user.
    """
    data_dir = Path.home() / ".local" / "share" / "downv"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DataDirectoryError(f"{exc.strerror or exc} ({exc.filename or data_dir})") from exc
    return data_dir
