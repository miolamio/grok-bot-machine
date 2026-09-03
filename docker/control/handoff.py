#!/usr/bin/env python3
"""Shared human-handoff flag for gbm-control (:7070) and connect-cu (:1337).

File present and valid ⇒ GUI is frozen (clicks, type, screenshots).
Shell and tree-only observe stay allowed. Human uses noVNC on the same desk.

This is our stand-in for the original app after request_box_help — not an RPC
on :1337. Original tool keys: instruction (required), reason, domain, idp_domain.
Chat cards and host-PC permissions are not this file.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

PATH = os.environ.get("GBM_HANDOFF_PATH", "/tmp/gbm-handoff.json")
NOVNC = os.environ.get(
    "GBM_NOVNC_URL",
    "http://127.0.0.1:6080/",
)
REASONS = ("auth", "captcha", "payment", "other")
REASON_ALIASES = {
    "login": "auth",
    "2fa": "auth",
    "sso": "auth",
    "unclear": "other",
}
NOT_DESK_KINDS = frozenset({"chat", "card", "host", "host_pc", "mac", "pc"})
GUI_TYPES = frozenset({"cdp", "a11y", "xdotool", "screenshot"})
AFTER_SHOT = "/workspace/handoff-after.png"
BEFORE_SHOT = "/workspace/handoff.png"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    r = str(reason).strip().lower()[:64]
    if not r:
        return None
    r = REASON_ALIASES.get(r, r)
    if r not in REASONS:
        return "other"
    return r


def parse_kind(kind: str | None) -> tuple[str, str | None]:
    """Return (kind, error_code). error_code is None when kind is desk."""
    k = (kind or "desk").strip().lower() or "desk"
    if k == "desk":
        return "desk", None
    if k in NOT_DESK_KINDS:
        return k, "not_desk"
    return k, "unknown_kind"


def normalize_host(value: str | None) -> str | None:
    t = (value or "").strip()[:253]
    if not t:
        return None
    if "://" in t:
        t = t.split("://", 1)[1]
    t = t.split("/")[0].split("?")[0].split(":")[0].strip().lower()
    return t or None


def instruction_of(d: dict[str, Any] | None) -> str:
    if not d:
        return ""
    return str(d.get("instruction") or d.get("message") or "").strip()


def read() -> dict[str, Any] | None:
    try:
        with open(PATH) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(d, dict) or not instruction_of(d):
        return None
    return d


def begin(
    instruction: str,
    reason: str | None = None,
    screenshot: str | None = None,
    domain: str | None = None,
    idp_domain: str | None = None,
    kind: str = "desk",
) -> dict[str, Any]:
    parsed, err = parse_kind(kind)
    if err:
        raise ValueError(err)
    inst = (instruction or "").strip()[:500]
    if not inst:
        raise ValueError("instruction_required")
    state: dict[str, Any] = {
        "kind": parsed,
        "instruction": inst,
        "since": _now(),
        "novnc": NOVNC,
    }
    why = normalize_reason(reason)
    if why:
        state["reason"] = why
    host = normalize_host(domain)
    if host:
        state["domain"] = host
    idp = normalize_host(idp_domain)
    if idp:
        state["idp_domain"] = idp
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
