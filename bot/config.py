"""Load config.yaml into a simple attribute-access object."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Cfg(dict):
    """dict with attribute access, recursive."""

    def __getattr__(self, k: str) -> Any:
        try:
            v = self[k]
        except KeyError as e:
            raise AttributeError(k) from e
        return Cfg(v) if isinstance(v, dict) else v

    def get_path(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def load(path: str | Path | None = None) -> Cfg:
    path = Path(path or os.environ.get("CRYPTOBOT_CONFIG", ROOT / "config.yaml"))
    with open(path) as f:
        raw = yaml.safe_load(f)
    cfg = Cfg(raw)
    if cfg.mode not in ("paper",):
        # Deliberate: live mode is not wired up. Paper only until proven.
        raise ValueError(f"mode={cfg.mode!r} not supported. This bot is paper-only by design.")
    return cfg
