"""AT-SPI snapshot and actions. Refs are @eN / @wN, valid until the next snapshot."""
from __future__ import annotations

import os
import subprocess
import time

WINDOWS: dict[str, dict] = {}
ELEMENTS: dict[str, dict] = {}
_WID = 0
_EID = 0

WALK_MAX_DEPTH = 14
WALK_MAX_ELEMENTS = 300
INTERESTING = {
    "push button", "toggle button", "link", "text", "entry", "password text",
    "list item", "menu item", "check box", "radio button", "combo box",
    "tab", "slider", "spin button", "tree item", "table cell", "heading",
    "page tab", "tool bar", "scroll bar", "image", "document web",
    "label", "icon", "list", "filler",
}

_ATSPI = None
_ATSPI_ERR = None


def _load() -> None:
    global _ATSPI, _ATSPI_ERR
    if _ATSPI is not None or _ATSPI_ERR is not None:
        return
    try:
        import gi  # type: ignore
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore
        if hasattr(Atspi, "init"):
            Atspi.init()
        _ATSPI = Atspi
    except Exception as e:  # pragma: no cover
        _ATSPI_ERR = str(e)


def available() -> bool:
    _load()
    return _ATSPI is not None


def error() -> str | None:
    _load()
    return _ATSPI_ERR


def child_count() -> int:
    _load()
    if _ATSPI is None:
        return -1
    try:
        desktop = _ATSPI.get_desktop(0)
        return int(desktop.get_child_count())
    except Exception:
        return -1


def apps() -> list[dict]:
    _load()
    if _ATSPI is None:
        return []
    out = []
    try:
        desktop = _ATSPI.get_desktop(0)
        n = desktop.get_child_count()
        for i in range(n):
            try:
                app = desktop.get_child_at_index(i)
                if app is None:
                    continue
                out.append({
                    "name": app.get_name() or "",
                    "pid": int(app.get_process_id()),
                    "role": app.get_role_name() or "",
                })
            except Exception:
                continue
    except Exception:
        return []
    return out


def _next_wid() -> str:
    global _WID
    _WID += 1
    return f"@w{_WID}"


def list_windows() -> list[dict]:
    global WINDOWS
    WINDOWS = {}
    r = subprocess.run(["wmctrl", "-lpG"], capture_output=True, text=True, timeout=5)
    active = subprocess.run(
        ["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=2,
    ).stdout.strip()
    result = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        wid_hex, _desk, pid, x, y, w, h, _host, title = parts
        try:
            wid_int = int(wid_hex, 16)
            info = {
                "ref": _next_wid(),
                "wid": wid_int,
                "title": title,
                "pid": int(pid),
                "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                "isFocused": bool(active) and int(active) == wid_int,
            }
        except ValueError:
            continue
        WINDOWS[info["ref"]] = info
        result.append({k: v for k, v in info.items() if k != "wid"})
    return result


def focused_window() -> dict | None:
    wins = list_windows()
    for w in wins:
        if w.get("isFocused"):
            return w
    return wins[0] if wins else None


def _is_editable(node) -> bool:
    if node is None or _ATSPI is None:
        return False
    try:
        if _ATSPI.Accessible.is_editable_text(node):
            return True
    except Exception:
        pass
    try:
        if hasattr(node, "is_editable_text") and node.is_editable_text():
            return True
    except Exception:
        pass
    return False


def _read_text(node) -> str:
    if node is None or _ATSPI is None:
        return ""
    try:
        n = int(_ATSPI.Text.get_character_count(node) or 0)
        if n <= 0:
            return ""
        return _ATSPI.Text.get_text(node, 0, n) or ""
    except TypeError:
        try:
            return node.get_text() or ""
        except Exception:
            return ""
    except Exception:
        return ""


def _set_text(node, text: str) -> bool:
    if node is None or _ATSPI is None:
        return False
    try:
        ret = _ATSPI.EditableText.set_text_contents(node, text)
        return ret is not False
    except Exception:
        pass
    try:
        n = int(_ATSPI.Text.get_character_count(node) or 0)
        if n > 0:
            _ATSPI.EditableText.delete_text(node, 0, n)
        _ATSPI.EditableText.insert_text(node, 0, text, len(text.encode("utf-8")))
        return True
    except Exception:
        return False


def _node_at(pid: int, path: list[int]):
    _load()
    if _ATSPI is None:
        return None
    try:
        desktop = _ATSPI.get_desktop(0)
        node = None
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is not None and app.get_process_id() == pid:
                node = app
                break
        if node is None:
            return None
        for idx in path:
            node = node.get_child_at_index(idx)
            if node is None:
                return None
        return node
    except Exception:
        return None


def _walk(pid: int, win_x: int = 0, win_y: int = 0) -> list[dict]:
    _load()
    if _ATSPI is None:
        return []
    out: list[dict] = []
    try:
        desktop = _ATSPI.get_desktop(0)
        target = None
        for i in range(desktop.get_child_count()):
            try:
                app = desktop.get_child_at_index(i)
                if app is not None and app.get_process_id() == pid:
                    target = app
                    break
            except Exception:
                continue
        if target is None:
            return []

        def visit(node, depth: int, path: list[int]) -> None:
            if len(out) >= WALK_MAX_ELEMENTS or depth > WALK_MAX_DEPTH:
                return
            try:
                role = (node.get_role_name() or "").lower()
            except Exception:
                role = ""
            try:
                name = node.get_name() or ""
            except Exception:
                name = ""
            editable = _is_editable(node)
            actions: list[str] = []
            try:
                ai = node.get_action_iface()
                if ai is not None:
                    for k in range(ai.get_n_actions()):
                        try:
                            nm = _ATSPI.Action.get_action_name(ai, k) or ai.get_name(k) or ""
                        except Exception:
                            nm = ""
                        if nm:
                            actions.append(nm.lower())
            except Exception:
                pass
            keep = (
                role in INTERESTING
                or depth <= 2
                or editable
                or any(a in {"press", "click", "activate", "edit"} for a in actions)
            )
            if keep:
                try:
                    ext = node.get_extents(_ATSPI.CoordType.SCREEN)
                    ex, ey, ew, eh = ext.x, ext.y, ext.width, ext.height
                    if (ex == 0 and ey == 0) or ew == 0 or eh == 0:
                        try:
                            ewnd = node.get_extents(_ATSPI.CoordType.WINDOW)
                            if ewnd.width > 0 and ewnd.height > 0:
                                ex, ey, ew, eh = ewnd.x + win_x, ewnd.y + win_y, ewnd.width, ewnd.height
                        except Exception:
                            pass
                    if ew > 0 and eh > 0 and abs(ex) < 100000 and abs(ey) < 100000:
                        out.append({
                            "role": role,
                            "name": name[:120],
                            "x": ex, "y": ey, "w": ew, "h": eh,
                            "actions": actions,
                            "canPress": any(a in {"press", "click", "activate"} for a in actions),
                            "canSetValue": editable,
                            "value": _read_text(node)[:200] if editable else "",
                            "_node": node,
                            "_pid": pid,
                            "_path": list(path),
                        })
                except Exception:
                    pass
            try:
                nc = node.get_child_count()
            except Exception:
                nc = 0
            for j in range(nc):
                if len(out) >= WALK_MAX_ELEMENTS:
                    return
                try:
                    child = node.get_child_at_index(j)
                except Exception:
                    continue
                if child is not None:
                    visit(child, depth + 1, path + [j])

        visit(target, 0, [])
    except Exception:
        return out
    return out


def snapshot(window_ref: str | None = None) -> dict:
    global ELEMENTS, _EID
    wins = list_windows()
    win = None
    if window_ref:
        win = WINDOWS.get(window_ref)
    else:
        win = next((WINDOWS[w["ref"]] for w in wins if w.get("isFocused")), None)
        if win is None and wins:
            win = WINDOWS[wins[0]["ref"]]
    ELEMENTS = {}
    _EID = 0
    public = []
    if win:
        walk = _walk(win["pid"], win["x"], win["y"])
        for el in walk:
            _EID += 1
            ref = f"@e{_EID}"
            ELEMENTS[ref] = el
            public.append({
                "ref": ref,
                "role": el["role"],
                "name": el["name"],
                "x": el["x"], "y": el["y"], "w": el["w"], "h": el["h"],
                "canPress": el["canPress"],
                "canSetValue": el["canSetValue"],
                "value": el.get("value") or "",
                "actions": el["actions"],
            })
    return {
        "window": {k: v for k, v in win.items() if k != "wid"} if win else None,
        "windows": wins,
        "elements": public,
    }


def _center(ref: str) -> tuple[int, int]:
    if ref.startswith("@e"):
        el = ELEMENTS.get(ref)
        if not el:
            raise ValueError(f"unknown element {ref}")
        return el["x"] + el["w"] // 2, el["y"] + el["h"] // 2
    if ref.startswith("@w"):
        w = WINDOWS.get(ref)
        if not w:
            raise ValueError(f"unknown window {ref}")
        return w["x"] + w["w"] // 2, w["y"] + w["h"] // 2
    raise ValueError(f"bad ref {ref}")


def click(ref: str) -> dict:
    el = ELEMENTS.get(ref)
    used = "xdotool"
    if el and el.get("canPress"):
        node = el.get("_node")
        try:
            ai = node.get_action_iface()
            if ai is not None and ai.get_n_actions() > 0:
                ai.do_action(0)
                used = "atspi"
        except Exception:
            used = "xdotool"
    if used != "atspi":
        cx, cy = _center(ref)
        subprocess.run(
            ["xdotool", "mousemove", str(cx), str(cy), "click", "1"],
            check=False, timeout=5, capture_output=True,
        )
        return {"ref": ref, "used": used, "x": cx, "y": cy}
    return {"ref": ref, "used": used}


def _grab_focus(node) -> None:
    if node is None:
        return
    try:
        ci = node.get_component_iface() if hasattr(node, "get_component_iface") else None
        if ci is not None:
            ci.grab_focus()
    except Exception:
        pass


def _live_node(el):
    path = el.get("_path")
    pid = el.get("_pid")
    if isinstance(path, list) and pid is not None:
        fresh = _node_at(int(pid), path)
        if fresh is not None:
            el["_node"] = fresh
            return fresh
    return el.get("_node")


def get_text(ref: str) -> dict:
    el = ELEMENTS.get(ref)
    if not el:
        raise ValueError(f"unknown element {ref}")
    node = _live_node(el)
    value = _read_text(node)
    return {"ref": ref, "value": value, "editable": _is_editable(node)}


def set_value(ref: str, text: str) -> dict:
    el = ELEMENTS.get(ref)
    if not el:
        raise ValueError(f"unknown element {ref}")
    node = _live_node(el)
    used = "fallback"
    err = None
    if node is not None and not _is_editable(node):
        try:
            ai = node.get_action_iface()
            if ai is not None:
                for k in range(ai.get_n_actions()):
                    try:
                        nm = (_ATSPI.Action.get_action_name(ai, k) or "").lower()
                    except Exception:
                        nm = ""
                    if nm in {"activate", "edit", "press", "click"}:
                        ai.do_action(k)
                        break
                node = _live_node(el)
        except Exception as e:
            err = str(e)
    if _is_editable(node):
        _grab_focus(node)
        if _set_text(node, text):
            used = "atspi"
            time.sleep(0.05)
            node = _live_node(el)
        else:
            err = err or "EditableText set_text_contents/insert_text failed"
    if used != "atspi":
        cx, cy = _center(ref)
        subprocess.run(
            ["xdotool", "mousemove", str(cx), str(cy), "click", "1",
             "key", "ctrl+a", "key", "Delete", "type", "--delay", "8", "--", text],
            check=False, timeout=15, capture_output=True,
        )
    value = _read_text(_live_node(el))
    out = {"ref": ref, "used": used, "len": len(text), "value": value}
    if err and used != "atspi":
        out["error"] = err
    return out


def perform_action(ref: str, name: str | None = None) -> dict:
    el = ELEMENTS.get(ref)
    if not el:
        raise ValueError(f"unknown element {ref}")
    node = el.get("_node")
    ai = node.get_action_iface() if node is not None else None
    if ai is None or ai.get_n_actions() == 0:
        raise ValueError(f"{ref} has no AT-SPI actions")
    idx = 0
    if name:
        want = name.lower()
        for k in range(ai.get_n_actions()):
            try:
                nm = (_ATSPI.Action.get_action_name(ai, k) or "").lower()
            except Exception:
                nm = ""
            if nm == want:
                idx = k
                break
    ai.do_action(idx)
    return {"ref": ref, "index": idx}


def bus_ok() -> bool:
    if os.environ.get("NO_AT_BRIDGE") == "1":
        return False
    try:
        r = subprocess.run(
            ["xprop", "-root", "AT_SPI_BUS"],
            capture_output=True, text=True, timeout=2,
        )
        return "AT_SPI_BUS" in r.stdout and r.returncode == 0
    except Exception:
        return False
