#!/usr/bin/env python3
"""Shared human-handoff flag for gbm-control (:7070) and connect-cu (:1337).

File present and valid ⇒ GUI is frozen (clicks, type, screenshots).
Shell and tree-only observe stay allowed. Human uses noVNC on the same desk.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

PATH = os.environ.get("GBM_HANDOFF_PATH", "/tmp/gbm-handoff.json")
NOVNC = os.environ.get(
    "GBM_NOVNC_URL",
    "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=off",
)
REASONS = ("captcha", "login", "2fa", "payment", "unclear", "other")
GUI_TYPES = frozenset({"cdp", "a11y", "xdotool", "screenshot"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read() -> dict[str, Any] | None:
    try:
        with open(PATH) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(d, dict) or not d.get("reason"):
        return None
    return d


def begin(reason: str, message: str = "", screenshot: str | None = None) -> dict[str, Any]:
    r = (reason or "other").strip().lower()[:64] or "other"
    if r not in REASONS:
        r = "other"
    state: dict[str, Any] = {
        "reason": r,
        "message": (message or "")[:500],
        "since": _now(),
        "novnc": NOVNC,
    }
    if screenshot:
        state["screenshot"] = screenshot
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PATH)
    try:
        os.chmod(PATH, 0o644)
    except OSError:
        pass
    return state


def clear() -> None:
    try:
        os.remove(PATH)
    except OSError:
        pass


def blocks_steps(steps: list) -> dict[str, Any] | None:
    h = read()
    if not h:
        return None
    for s in steps or []:
        if isinstance(s, dict) and s.get("type") in GUI_TYPES:
            return h
    return None
