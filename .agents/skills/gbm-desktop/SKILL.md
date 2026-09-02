---
name: gbm-desktop
description: >
  Drive the grok-bot-machine Docker desktop from the host via ./scripts/gbm
  (observe, shell, AT-SPI, CDP, xdotool). Use when the user wants computer use,
  to click/type in the container, inspect the XFCE/Xvfb desk, run commands as
  box, or operate Thunar/GTK — not the Mac display. Prefer this over raw curl
  or MCP unless the host only speaks MCP.
compatibility: Requires Docker, python3, and a running grok-bot container (compose).
---

# gbm-desktop

Operate the **container** desk (`DISPLAY=:1`, 1280×800), never the Mac.

CLI, from the grok-bot-machine repo root:

```bash
./scripts/gbm <command>
```

`--help` is the flag source of truth. JSON on stdout. PNG is written to a file; do not paste base64 into chat.

## Bring-up

```bash
docker compose up -d --build   # if the box is not running
./scripts/gbm ready
./scripts/gbm doctor           # blockers must be []; can_cdp is false until Chrome starts
```

noVNC (human watch only): `http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=off`

## What to call

| Goal | Command |
| --- | --- |
| Readiness | `./scripts/gbm doctor` |
| Tree of focused app | `./scripts/gbm observe` |
| Command as user `box` in `/workspace` | `./scripts/gbm shell '…'` |
| Batched steps | `./scripts/gbm act '[{"type":"…"}]'` |
| Desk PNG | `./scripts/gbm screenshot -o workspace/desk.png` |
| Pointer | `./scripts/gbm mouse 640 400` / `./scripts/gbm click 640 400` |
| Type / key | `./scripts/gbm type 'text'` / `./scripts/gbm key Return` |

Router (pick the first that fits):

1. **shell** — files, packages, launch apps (`thunar /workspace &`, `box-chrome URL &`). Do not type commands into `xfce4-terminal`.
2. **cdp** — Chromium is up (`doctor.chromium.debug_ok` or `can_cdp`). Snapshot → click/fill by **ref**, not pixels. Launch: `./scripts/gbm shell 'box-chrome >/tmp/chrome.log 2>&1 &'` then wait.
3. **a11y** — GTK/Thunar. `{"type":"a11y","action":"snapshot"}` then `click` / `set_value` / `perform_action` by ref. `set_value` is AT-SPI, not `xdotool type`.
4. **xdotool** — no tree. Coords origin top-left, x 0–1279, y 0–799.
5. **screenshot** — last resort, or when the user asks to see the desk.

Unknown `type` in a batch applies **nothing**. Max 20 steps. Shell success is `ok` **and** `exit == 0`.

Pixel vision loop (PNG → click) is a different contract: load **gbm-grok-cu**.

## Auth

`:7070` uses `GBM_CONTROL_TOKEN` (compose default `dev-local-token`). The CLI also reads the token from the container. `:1337` is `Bearer local` — only for `./scripts/gbm connect …`.

## Gotchas

- Idle has no Chrome; CDP empty until `box-chrome`.
- Pointer moves inside Xvfb, not on the Mac.
- `resize=off` on noVNC or coords drift.
- `/workspace` on the host is `./workspace`.
- Killing the CLI does not kill the desktop.
