"""Root-window PNG capture. Prefer xcapture (MIT-SHM), then ffmpeg."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

DISPLAY = os.environ.get("DISPLAY", ":1")
_XCAPTURE = os.environ.get("GBM_XCAPTURE", "/usr/local/bin/xcapture")
_BACKEND = "none"
_LAST_MS = 0.0


def last_backend() -> str:
    return _BACKEND


def last_ms() -> float:
    return _LAST_MS


def _cursor() -> dict:
    try:
        r = subprocess.run(
            ["xdotool", "getmouselocation", "--shell"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        vals = dict(
            line.split("=", 1) for line in r.stdout.splitlines() if "=" in line
        )
        return {
            "x": int(vals.get("X", "0")),
            "y": int(vals.get("Y", "0")),
            "screen": int(vals.get("SCREEN", "0")),
        }
    except Exception:
        return {"x": 0, "y": 0, "screen": 0}


def _active_window() -> dict:
    try:
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout.strip()
        if not wid:
            return {}
        name = subprocess.run(
            ["xdotool", "getwindowname", wid],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout.strip()
        geom = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", wid],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout
        vals = dict(line.split("=", 1) for line in geom.splitlines() if "=" in line)
        return {
            "id": int(wid),
            "title": name,
            "x": int(vals.get("X", "0")),
            "y": int(vals.get("Y", "0")),
            "width": int(vals.get("WIDTH", "0")),
            "height": int(vals.get("HEIGHT", "0")),
        }
    except Exception:
        return {}


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def grab() -> dict:
    global _BACKEND, _LAST_MS
    t0 = time.perf_counter()
    png, backend = _grab_png()
    _LAST_MS = (time.perf_counter() - t0) * 1000.0
    _BACKEND = backend
    w, h = _png_size(png)
    return {
        "png": png,
        "backend": backend,
        "width": w,
        "height": h,
        "ms": round(_LAST_MS, 2),
        "cursor": _cursor(),
        "activeWindow": _active_window(),
    }


def _grab_png() -> tuple[bytes, str]:
    if os.path.isfile(_XCAPTURE) and os.access(_XCAPTURE, os.X_OK):
        r = subprocess.run(
            [_XCAPTURE, DISPLAY],
            capture_output=True, timeout=5, check=False,
        )
        if r.returncode == 0 and r.stdout.startswith(b"\x89PNG"):
            m = re.search(rb"XCAPTURE_BACKEND=(\S+)", r.stderr)
            backend = m.group(1).decode() if m else "xcapture"
            return r.stdout, backend
    if shutil.which("ffmpeg"):
        r = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "x11grab", "-video_size", "1280x800",
                "-i", DISPLAY, "-frames:v", "1", "-f", "image2",
                "-vcodec", "png", "pipe:1",
            ],
            capture_output=True, timeout=8, check=False,
        )
        if r.returncode == 0 and r.stdout.startswith(b"\x89PNG"):
            return r.stdout, "ffmpeg"
        raise RuntimeError(f"ffmpeg capture failed: {r.stderr[-400:]!r}")
    raise RuntimeError("no capture backend (xcapture/ffmpeg)")
