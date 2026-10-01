"""Config file loader for ragpreflight (.ragpreflight.toml in TOML format).

Searches for .ragpreflight.toml in the current directory, then HOME directory.
CLI flags always override config file values.

Usage:
    from ragpreflight.config import load_config

    cfg = load_config()
    profile = cfg.get("profile", "standard")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Keys we accept in the config file (guards against typos / unknown keys)
_KNOWN_KEYS = frozenset(
    {
        "profile",
        "max_file_size_mb",
        "hard_max_file_size_mb",
        "stale_months",
        "output_format",
        "quiet",
        "chunk_size",
        "chunk_overlap",
        "chunk_strategy",
    }
)

_CONFIG_FILENAME = ".ragpreflight.toml"


def find_config_file() -> Path | None:
    """Locate .ragpreflight.toml in cwd or home directory.

    Returns:
        Path to the first .ragpreflight.toml found, or None.
    """
    for directory in (Path.cwd(), Path.home()):
        candidate = directory / _CONFIG_FILENAME
        if candidate.is_file():
            logger.debug("Found config file: %s", candidate)
            return candidate
    return None


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load .ragpreflight.toml config file.

    Searches cwd → home unless ``config_path`` is given explicitly.

    Args:
        config_path: Explicit path to a config file (skips auto-discovery).

    Returns:
        Dict of config values. Empty dict if no config found.
    """
    path = config_path or find_config_file()
    if path is None:
        return {}

    try:
        return _parse_toml(path)
    except Exception as exc:
        logger.warning("Could not parse config file '%s': %s", path, exc)
        return {}


def _parse_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML config file with stdlib (Python 3.11+) or tomli fallback.

    Args:
        path: Path to the TOML file.

    Returns:
        Parsed config dict (only known keys retained).

    Raises:
        Exception: If the file cannot be parsed.
    """
    raw: dict[str, Any] = {}

    try:
        # Python 3.11+ has tomllib in stdlib
        import tomllib  # type: ignore[import]  # available from 3.11

        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except ImportError:
        # Fall back to tomli (pip install tomli) or manual INI-style parser
        try:
            import tomli  # type: ignore[import]

            with open(path, "rb") as f:
                raw = tomli.load(f)
        except ImportError:
            # Last resort: simple key=value parser (handles basic cases)
            raw = _simple_kv_parse(path)

    # Flatten [ragpreflight] section if present, else use top-level
    config = raw.get("ragpreflight", raw)

    # Warn about unknown keys
    for key in config:
        if key not in _KNOWN_KEYS:
            logger.warning(
                "Unknown config key '%s' in %s — ignored. Valid keys: %s",
                key,
                path,
                ", ".join(sorted(_KNOWN_KEYS)),
            )

    return {k: v for k, v in config.items() if k in _KNOWN_KEYS}


def _simple_kv_parse(path: Path) -> dict[str, Any]:
    """Minimal key = value parser for environments without tomllib/tomli.

    Handles:
        key = "string"
        key = 42
        key = 3.14
        key = true / false
        # comments
        [section] headers (ignored — treated as flat)

    Args:
        path: Path to the file.

    Returns:
        Dict of parsed values.
    """
    result: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Type coercion
        if value.lower() == "true":
            result[key] = True
        elif value.lower() == "false":
            result[key] = False
        else:
            try:
                result[key] = int(value)
            except ValueError:
                try:
                    result[key] = float(value)
                except ValueError:
                    result[key] = value
    return result
