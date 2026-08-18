"""Internal config helpers shared by the economy package.

The demo contract requires every tunable number to live in
``demo/config/*.json``.  These helpers load and navigate those files.  They are
private to the ``economy`` package and are not part of the cross-module API.
"""
from __future__ import annotations

import json
from pathlib import Path

# Ledger resource keys, as defined in the demo contract section 3.2.
RESOURCE_KEYS = ("qian", "lingshi", "neili", "shengwang")


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_config_dir(config_dir: str, package_dir: Path) -> Path:
    """Resolve ``config_dir`` first against CWD, then against the demo dir."""
    candidate = Path(config_dir)
    if candidate.is_absolute():
        return candidate
    cwd_candidate = Path.cwd() / candidate
    if (cwd_candidate / "balance.json").exists():
        return cwd_candidate
    pkg_candidate = package_dir / candidate
    if (pkg_candidate / "balance.json").exists():
        return pkg_candidate
    # Not found yet: return the CWD candidate so the caller can raise a clear error.
    return cwd_candidate


def dget(cfg: dict, dotted: str, default=None):
    """Fetch a nested value by dotted path, e.g. ``economy.exchange.lingshi_to_qian``."""
    node = cfg
    for part in str(dotted).split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return default
    return node


def as_number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
