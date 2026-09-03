#!/usr/bin/env python3
"""CLI for grok-bot-machine. Same binary on the host and in the box.

Host:  ./scripts/gbm …     → 127.0.0.1:7070 and :1337
In-box: gbm-act …          → http://127.0.0.1:7070

Does not start an LLM. Does not drive the Mac display.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "http://127.0.0.1:7070"
DEFAULT_CONNECT = "http://127.0.0.1:1337"
DEFAULT_CONNECT_TOKEN = "local"
CONTAINER = os.environ.get("GBM_CONTAINER", "grok-bot")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _load_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip("'").strip('"')
    except OSError:
        pass
    return out


def _docker_token(container: str) -> str:
    try:
        r = subprocess.run(
            ["docker", "exec", container, "cat", "/tmp/gbm-control.token"],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def resolve_token(explicit: str | None, container: str) -> str:
    if explicit:
        return explicit
    t = os.environ.get("GBM_CONTROL_TOKEN", "").strip()
    if t:
        return t
    for path in ("/tmp/gbm-control.token", os.path.expanduser("~/.gbm-control.token")):
        try:
            with open(path) as f:
                t = f.read().strip()
            if t:
                return t
        except OSError:
            pass
    env = _load_env_file("/tmp/gbm.env")
    t = env.get("GBM_CONTROL_TOKEN", "").strip()
    if t:
        return t
    return _docker_token(container)


def resolve_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    t = os.environ.get("GBM_CONTROL_URL", "").strip()
    if t:
        return t.rstrip("/")
    env = _load_env_file("/tmp/gbm.env")
    return (env.get("GBM_CONTROL_URL") or DEFAULT_URL).rstrip("/")


def resolve_connect_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    t = os.environ.get("GBM_CONNECT_URL", "").strip()
    return (t or DEFAULT_CONNECT).rstrip("/")


def resolve_connect_token(explicit: str | None) -> str:
    if explicit:
        return explicit
    t = os.environ.get("GBM_CONNECT_TOKEN", "").strip()
    return t or DEFAULT_CONNECT_TOKEN


def default_out(name: str) -> str:
    root = os.environ.get("GBM_ROOT", "").strip()
    if root:
        return os.path.join(root, "workspace", name)
    if os.path.isdir("workspace"):
        return os.path.join("workspace", name)
    return os.path.join("/tmp", name)


def write_png_b64(b64: str, path: str) -> dict[str, Any]:
    raw = base64.b64decode(b64)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw)
    info: dict[str, Any] = {"path": path, "bytes": len(raw)}
    if raw[:8] == PNG_MAGIC and len(raw) >= 24:
        info["width"] = int.from_bytes(raw[16:20], "big")
        info["height"] = int.from_bytes(raw[20:24], "big")
    return info


def persist_pngs(obj: Any, path: str) -> list[dict[str, Any]]:
    """Replace bulky PNG fields with a saved path. First PNG wins the path."""
    saved: list[dict[str, Any]] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if not isinstance(v, str) or len(v) < 32:
                    walk(v)
                    continue
                b64_png = v.startswith("iVBOR")
                looks_png = k in ("pngBase64", "png_base64") or (k == "screenshot" and b64_png)
                if not looks_png:
                    walk(v)
                    continue
                dest = path if not saved else f"{os.path.splitext(path)[0]}-{len(saved)}{os.path.splitext(path)[1] or '.png'}"
                try:
                    info = write_png_b64(v, dest)
                except Exception:
                    walk(v)
                    continue
                o[k] = info
                saved.append(info)
        elif isinstance(o, list):
            for item in o:
                walk(item)

    walk(obj)
    return saved


def http(
    url: str,
    token: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    full = url + path
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(full, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode() or "null")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(err)
        except json.JSONDecodeError:
            parsed = {"error": err[:800], "status": e.code}
        payload = parsed if isinstance(parsed, dict) else {"error": parsed}
        if "ok" not in payload:
            payload = {"ok": False, "status": e.code, "error": payload}
        else:
            payload.setdefault("status", e.code)
        emit(payload)
        raise SystemExit(1)
    except urllib.error.URLError as e:
        raise SystemExit(json.dumps({"ok": False, "error": f"unreachable {full}: {e}"}, ensure_ascii=False))


def connect_post(url: str, token: str, path: str, body: dict | None = None, timeout: float = 60.0) -> Any:
    return http(
        url, token, "POST", path, body or {}, timeout=timeout,
        extra_headers={"Connect-Protocol-Version": "1"},
    )


def emit(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


def _need_token(token: str) -> None:
    if not token:
        emit({"ok": False, "error": "no token (set GBM_CONTROL_TOKEN, or start grok-bot so docker exec can read /tmp/gbm-control.token)"})
        raise SystemExit(2)


def _act(url: str, token: str, steps: list, timeout: float) -> dict:
    return http(url, token, "POST", "/act", {"steps": steps}, timeout=timeout)


def _exit_act(body: Any) -> int:
    if isinstance(body, dict) and body.get("ok") is False:
        emit(body)
        return 1
    emit(body)
    return 0


def main(argv: list[str] | None = None) -> int:
    base = os.path.basename(sys.argv[0])
    prog = os.environ.get("GBM_CLI_PROG") or (
        "gbm-act" if base.startswith("gbm-act") else "gbm"
    )
    p = argparse.ArgumentParser(
        prog=prog,
        description="Drive the grok-bot Docker desktop (not the Mac). Native API :7070, Connect-RPC :1337.",
    )
    p.add_argument("--url", default=None, help="Native control URL (default GBM_CONTROL_URL or http://127.0.0.1:7070)")
    p.add_argument("--token", default=None, help="Bearer for :7070")
    p.add_argument("--connect-url", default=None, help="Connect-RPC URL (default GBM_CONNECT_URL or http://127.0.0.1:1337)")
    p.add_argument("--connect-token", default=None, help="Bearer for :1337 (default local)")
    p.add_argument("--container", default=CONTAINER, help="Docker name for token fallback (default grok-bot)")
    p.add_argument("--timeout", type=float, default=60.0)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="GET /health (no auth)")
    sub.add_parser("ready", help="Poll /health until the box answers")
    sub.add_parser("doctor", help="GET /doctor")

    ob = sub.add_parser("observe", help="POST /observe (AT-SPI tree; pixels only with --screenshot)")
    ob.add_argument("--screenshot", action="store_true")
    ob.add_argument("--no-tree", action="store_true")
    ob.add_argument("--window", default=None, help="Window ref @wN")
    ob.add_argument("-o", "--out", default=None, help="Write PNG here instead of embedding base64")

    ac = sub.add_parser("act", help="POST /act with a JSON steps array")
    ac.add_argument("steps", help="JSON array of steps, or @path to a file")

    sh = sub.add_parser("shell", help="Run a command as box in /workspace")
    sh.add_argument("command")
    sh.add_argument("--cwd", default=None)

    shot = sub.add_parser("screenshot", help="Grab the 1280×800 desk PNG via :7070")
    shot.add_argument("-o", "--out", default=None)

    cl = sub.add_parser("click", help="xdotool click on the desk (native :7070)")
    cl.add_argument("x", type=int)
    cl.add_argument("y", type=int)
    cl.add_argument("--button", default="1")
    cl.add_argument("--count", type=int, default=1)

    mv = sub.add_parser("mouse", help="Move the container pointer")
    mv.add_argument("x", type=int)
    mv.add_argument("y", type=int)

    ty = sub.add_parser("type", help="xdotool type into the focused container widget")
    ty.add_argument("text")

    ky = sub.add_parser("key", help="xdotool key (e.g. Return, ctrl+a)")
    ky.add_argument("keys", nargs="+")

    ho = sub.add_parser(
        "handoff",
        help="Freeze GUI; human uses noVNC. Omit -m/--instruction to read status.",
    )
    ho.add_argument(
        "-m", "--instruction", "--message",
        dest="instruction",
        default=None,
        help="Required to start. Short string for the human, e.g. Sign in to Google",
    )
    ho.add_argument(
        "--reason",
        default=None,
        help="auth | captcha | payment | other (login/2fa → auth). Optional.",
    )
    ho.add_argument("--domain", default=None, help="App you were entering, e.g. drive.google.com")
    ho.add_argument("--idp-domain", default=None, help="IdP host if different, e.g. accounts.google.com")
    ho.add_argument(
        "--kind",
        default="desk",
        help="desk (freeze). chat/host are not a freeze — ask in conversation.",
    )
    ho.add_argument("--open", action="store_true", help="open noVNC in the Mac browser")
    ho.add_argument(
        "--wait",
        action="store_true",
        help="Block until resume (noVNC button or gbm resume). stdout is the final JSON.",
    )
    ho.add_argument(
        "--wait-timeout",
        type=float,
        default=1800.0,
        help="Seconds to wait with --wait (default 1800)",
    )
    rs = sub.add_parser("resume", help="End handoff; new turn looks at the desk")
    rs.add_argument("-o", "--out", default=None, help="Copy after-screenshot here")

    cs = sub.add_parser("connect", help="Grok-faithful Connect-RPC on :1337")
    csub = cs.add_subparsers(dest="ccmd", required=True)
    csub.add_parser("ping")
    csub.add_parser("caps")
    cx = csub.add_parser("exec", help="computer_use_args.actions JSON array")
    cx.add_argument("actions", help="JSON array, or @path")
    cx.add_argument("-o", "--out", default=None, help="Save screenshot PNG if present")
    cshot = csub.add_parser("screenshot")
    cshot.add_argument("-o", "--out", default=None)
    ccl = csub.add_parser("click")
    ccl.add_argument("x", type=int)
    ccl.add_argument("y", type=int)
    ccl.add_argument("--button", type=int, default=1)
    ccl.add_argument("--count", type=int, default=1)
    cmv = csub.add_parser("mouse")
    cmv.add_argument("x", type=int)
    cmv.add_argument("y", type=int)
    cty = csub.add_parser("type")
    cty.add_argument("text")
    cky = csub.add_parser("key")
    cky.add_argument("key")

    args = p.parse_args(argv)
    url = resolve_url(args.url)
    token = resolve_token(args.token, args.container)
    timeout = float(args.timeout)

    if args.cmd == "health":
        emit(http(url, "", "GET", "/health", timeout=min(timeout, 10)))
        return 0

    if args.cmd == "ready":
        import time
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = http(url, "", "GET", "/health", timeout=3)
                if isinstance(last, dict) and last.get("ok"):
                    emit(last)
                    return 0
            except (SystemExit, OSError, Exception) as e:
                last = {"ok": False, "error": str(e)}
            time.sleep(0.4)
        emit({"ok": False, "error": "control plane not ready", "last": last})
        return 1

    if args.cmd == "doctor":
        _need_token(token)
        emit(http(url, token, "GET", "/doctor", timeout=timeout))
        return 0

    if args.cmd == "observe":
        _need_token(token)
        body: dict[str, Any] = {}
        if args.screenshot:
            body["include_screenshot"] = True
        if args.no_tree:
            body["include_tree"] = False
        if args.window:
            body["window"] = args.window
        result = http(url, token, "POST", "/observe", body, timeout=timeout)
        out = args.out or (default_out("gbm-observe.png") if args.screenshot else None)
        if out:
            persist_pngs(result, out)
        emit(result)
        return 0

    if args.cmd == "act":
        _need_token(token)
        raw = args.steps
        if raw.startswith("@"):
            with open(raw[1:]) as f:
                raw = f.read()
        steps = json.loads(raw)
        if not isinstance(steps, list):
            emit({"ok": False, "error": "act expects a JSON array of steps"})
            return 2
        return _exit_act(_act(url, token, steps, timeout))

    if args.cmd == "shell":
        _need_token(token)
        step: dict[str, Any] = {"type": "shell", "cmd": args.command}
        if args.cwd:
            step["cwd"] = args.cwd
        return _exit_act(_act(url, token, [step], timeout))

    if args.cmd == "screenshot":
        _need_token(token)
        result = _act(url, token, [{"type": "screenshot"}], timeout)
        out = args.out or default_out("gbm-last.png")
        persist_pngs(result, out)
        return _exit_act(result)

    if args.cmd == "click":
        _need_token(token)
        steps = []
        for _ in range(max(1, args.count)):
            steps.append({"type": "xdotool", "action": "click", "x": args.x, "y": args.y, "button": str(args.button)})
        return _exit_act(_act(url, token, steps, timeout))

    if args.cmd == "mouse":
        _need_token(token)
        return _exit_act(_act(url, token, [{"type": "xdotool", "action": "mousemove", "x": args.x, "y": args.y}], timeout))

    if args.cmd == "type":
        _need_token(token)
        return _exit_act(_act(url, token, [{"type": "xdotool", "action": "type", "text": args.text}], timeout))

    if args.cmd == "key":
        _need_token(token)
        return _exit_act(_act(url, token, [{"type": "xdotool", "action": "key", "keys": args.keys}], timeout))

    if args.cmd == "handoff":
        _need_token(token)
        inst = (args.instruction or "").strip()
        extras = bool(
            args.reason
            or args.domain
            or args.idp_domain
            or ((args.kind or "desk") != "desk")
        )
        if not inst:
            if extras:
                emit({"ok": False, "error": "instruction_required"})
                return 2
            emit(http(url, token, "GET", "/handoff", timeout=min(timeout, 15)))
            return 0
        body: dict[str, Any] = {
            "instruction": inst,
            "kind": args.kind or "desk",
        }
        if args.reason:
            body["reason"] = args.reason
        if args.domain:
            body["domain"] = args.domain
        if args.idp_domain:
            body["idp_domain"] = args.idp_domain
        result = http(url, token, "POST", "/handoff", body, timeout=timeout)
        novnc = (result.get("handoff") or {}).get("novnc")
        if args.open and novnc:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([opener, novnc], check=False, timeout=8, capture_output=True)
        if result.get("ok") is False:
            emit(result)
            return 1
        if not args.wait:
            emit(result)
            return 0
        sys.stderr.write(
            "gbm: waiting for resume (noVNC «Give back to agent» or ./scripts/gbm resume)\n"
        )
        if novnc:
            sys.stderr.write("gbm: %s\n" % novnc)
        sys.stderr.flush()
        import time
        deadline = time.time() + max(1.0, float(args.wait_timeout))
        last: Any = result
        while time.time() < deadline:
            time.sleep(0.5)
            last = http(url, token, "GET", "/handoff", timeout=min(timeout, 15))
            if not last.get("handoff"):
                after = default_out("handoff-after.png")
                until = time.time() + 2.0
                while time.time() < until and not os.path.isfile(after):
                    time.sleep(0.1)
                out: dict[str, Any] = {"ok": True, "handoff": None, "waited": True}
                if os.path.isfile(after):
                    out["screenshot"] = after
                emit(out)
                return 0
        emit({"ok": False, "error": "handoff_timeout", "handoff": (last or {}).get("handoff")})
        return 1

    if args.cmd == "resume":
        _need_token(token)
        result = http(url, token, "POST", "/handoff/resume", {}, timeout=min(timeout, 15))
        if isinstance(result, dict) and isinstance(result.get("pngBase64"), str):
            persist_pngs(result, args.out or default_out("handoff-after.png"))
        emit(result)
        return 0 if result.get("ok") is not False else 1

    if args.cmd == "connect":
        curl = resolve_connect_url(args.connect_url)
        ctok = resolve_connect_token(args.connect_token)
        if args.ccmd == "ping":
            emit(connect_post(curl, ctok, "/agent.v1.ControlService/Ping", {}, timeout=min(timeout, 15)))
            return 0
        if args.ccmd == "caps":
            emit(connect_post(curl, ctok, "/agent.v1.ControlService/GetCapabilities", {}, timeout=min(timeout, 15)))
            return 0

        def exec_actions(actions: list, out: str | None) -> int:
            body = {"computerUseArgs": {"actions": actions}}
            result = connect_post(curl, ctok, "/agent.v1.ExecService/Exec", body, timeout=timeout)
            if out:
                persist_pngs(result, out)
            emit(result)
            return 0

        if args.ccmd == "exec":
            raw = args.actions
            if raw.startswith("@"):
                with open(raw[1:]) as f:
                    raw = f.read()
            actions = json.loads(raw)
            if not isinstance(actions, list):
                emit({"ok": False, "error": "connect exec expects a JSON array of actions"})
                return 2
            return exec_actions(actions, args.out)
        if args.ccmd == "screenshot":
            out = args.out or default_out("gbm-connect.png")
            return exec_actions([{"screenshot": {}}], out)
        if args.ccmd == "click":
            return exec_actions([{
                "click": {"coordinate": {"x": args.x, "y": args.y}, "button": args.button, "count": args.count},
            }], None)
        if args.ccmd == "mouse":
            return exec_actions([{"mouseMove": {"coordinate": {"x": args.x, "y": args.y}}}], None)
        if args.ccmd == "type":
            return exec_actions([{"type": {"text": args.text}}], None)
        if args.ccmd == "key":
            return exec_actions([{"key": {"key": args.key}}], None)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
