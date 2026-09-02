"""Path resolution for DownV output locations."""

import os
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


def resolve_output_directory(override: str | None = None) -> Path:
    """Resolve the base output directory, creating it if necessary.

    Precedence is ``override`` (the ``--output`` CLI value) followed by the
    ``DOWNV_OUTPUT_DIR`` environment variable, then the built-in default. When
    ``override`` is None and the environment variable is unset, this is exactly
    :func:`get_output_directory`.
    """
    candidate = override if override else os.environ.get("DOWNV_OUTPUT_DIR")
    if candidate:
        output_dir = Path(candidate).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputDirectoryError(
                f"{exc.strerror or exc} ({exc.filename or output_dir})"
            ) from exc
        return output_dir
    return get_output_directory()


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
