"""Configuration and timeout resolution utilities for indexing."""

import json
import os
import sys
from typing import Optional

DEFAULT_EXTRACT_TIMEOUT = 1800
DEFAULT_PROBE_TIMEOUT = 300
DEFAULT_PLEX_TIMEOUT = 120


def _load_config() -> Optional[dict]:
    """Load configuration from config.json if present.

    Returns:
        Optional[dict]: The parsed JSON configuration as a dictionary, or None
        if config.json does not exist.

    Raises:
        ValueError: If config.json does not contain a JSON object.
        OSError: If an error occurs while opening or reading config.json.
        json.JSONDecodeError: If config.json contains invalid JSON syntax.
    """
    if not os.path.exists("config.json"):
        return None
    with open("config.json", "r") as f:
        cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config.json must contain a JSON object.")
        return cfg


def _resolve_timeouts(
    extract_timeout: Optional[int] = None,
    probe_timeout: Optional[int] = None,
) -> tuple[Optional[int], Optional[int]]:
    """Resolve subprocess timeouts from explicit values, config.json, or defaults.

    Args:
        extract_timeout (Optional[int]): Subprocess timeout in seconds for extracting
            subtitle tracks. If None, checks config.json ('extractTimeout' or 'extract_timeout')
            or defaults to DEFAULT_EXTRACT_TIMEOUT (1800s). Set to 0 or negative for unlimited.
        probe_timeout (Optional[int]): Subprocess timeout in seconds for ffprobe.
            If None, checks config.json ('probeTimeout' or 'probe_timeout') or
            defaults to DEFAULT_PROBE_TIMEOUT (300s). Set to 0 or negative for unlimited.

    Returns:
        tuple[Optional[int], Optional[int]]: Tuple of (effective_extract_timeout, effective_probe_timeout).
    """
    indexer = sys.modules.get("hagi.indexer")
    load_cfg = getattr(indexer, "_load_config", _load_config) if indexer else _load_config

    try:
        config = load_cfg() or {}
    except Exception as e:
        print(f"Error reading config.json: {e}")
        config = {}

    if extract_timeout is not None:
        effective_extract = None if extract_timeout <= 0 else extract_timeout
    else:
        cfg_extract = config.get("extractTimeout", config.get("extract_timeout"))
        if cfg_extract is not None:
            try:
                cfg_extract_val = int(cfg_extract)
                effective_extract = None if cfg_extract_val <= 0 else cfg_extract_val
            except (ValueError, TypeError, OverflowError):
                effective_extract = DEFAULT_EXTRACT_TIMEOUT
        else:
            effective_extract = DEFAULT_EXTRACT_TIMEOUT

    if probe_timeout is not None:
        effective_probe = None if probe_timeout <= 0 else probe_timeout
    else:
        cfg_probe = config.get("probeTimeout", config.get("probe_timeout"))
        if cfg_probe is not None:
            try:
                cfg_probe_val = int(cfg_probe)
                effective_probe = None if cfg_probe_val <= 0 else cfg_probe_val
            except (ValueError, TypeError, OverflowError):
                effective_probe = DEFAULT_PROBE_TIMEOUT
        else:
            effective_probe = DEFAULT_PROBE_TIMEOUT

    return effective_extract, effective_probe
