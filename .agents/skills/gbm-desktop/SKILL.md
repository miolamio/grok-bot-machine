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
| Human takeover (desk kit) | `./scripts/gbm handoff -m 'Sign in to Google' [--reason auth] [--domain] [--idp-domain]` then **stop the turn**; after the human says done, `resume` and screenshot |

Router (pick the first that fits):

1. **shell** — files, packages, launch apps (`thunar /workspace &`, `box-chrome URL &`). Do not type commands into `xfce4-terminal`.
2. **cdp** — Chromium is up (`doctor.chromium.debug_ok` or `can_cdp`). Snapshot → click/fill by **ref**, not pixels. Launch: `./scripts/gbm shell 'box-chrome >/tmp/chrome.log 2>&1 &'` then wait.
3. **a11y** — GTK/Thunar. `{"type":"a11y","action":"snapshot"}` then `click` / `set_value` / `perform_action` by ref. `set_value` is AT-SPI, not `xdotool type`.
4. **xdotool** — no tree. Coords origin top-left, x 0–1279, y 0–799.
5. **screenshot** — last resort, or when the user asks to see the desk.

Unknown `type` in a batch applies **nothing**. Max 20 steps. Shell success is `ok` **and** `exit == 0`.

Pixel vision loop (PNG → click) is a different contract: load **gbm-grok-cu**.

## Three kits (pick one; do not mix)

The box has **no human RPC**. Shell never means “call a person”. `doctor` is X/Chrome health, not a human signal. You decide, then pick a kit.

| Kit | Freeze the desk? | What you do |
| --- | --- | --- |
| **desk** | yes | `./scripts/gbm handoff` then **stop the turn** |
| **chat** | no | Ask in this conversation. Do not call `handoff`. |
| **host** | no | Ask for a Mac permission. Do not freeze XFCE. |

### Desk — human on the 1280×800 noVNC

Call when the *container* GUI is stuck on a secret or a wall you must not operate. You call the tool; a CU worker report (“cannot as the user”) is not the call.

- Login / SSO / passkey / SMS / TOTP / 2FA → `--reason auth`
- Captcha / “I'm not a robot” / Cloudflare → `--reason captcha`
- Apple Pay / card / 3DS → `--reason payment`
- Cookie wall, GDPR Accept, Chrome “save password” → `--reason other` or omit reason
- One reason only. SSO+captcha: whatever is **on screen now** (usually `auth` first)

Do **not** type passwords, 2FA, captchas, or card numbers. Do **not** screenshot while they type.

```bash
./scripts/gbm handoff -m 'Sign in to Google' --reason auth \
  --domain drive.google.com --idp-domain accounts.google.com
# instruction (-m) is required. reason/domain/idp_domain are optional.
# domain = the app you were entering; idp_domain = the identity bar, only if different.
# Direct Google login: --domain only, no --idp-domain.
```

Then **stop the turn**. No more tool calls until the human says they are done. Do not retry 409. Do not `resume` yourself.

New turn, after they say done:

```bash
./scripts/gbm resume
./scripts/gbm screenshot -o workspace/desk.png   # look at the live frame
```

Do not reuse `handoff.png`. Do not assume login succeeded: if the frame is still a wall, call `handoff` again with a new instruction. `--open` launches noVNC on the Mac. Until resume: GUI steps and Connect Exec are **409**; `shell` and tree-only `observe` still work.

### Chat — question only, desk stays yours

Ask the user here. Keep clicking if the task allows. Examples:

- “may I run this command?”
- send an email / post in chat / pay / install a plugin / delete a routine
- connector `needsAuth` that is not a page on this desk

`./scripts/gbm handoff --kind chat` is rejected (`not_desk`). That is intentional.

### Host — Mac, not the box

Camera, mic, Documents/Desktop/Downloads on the Mac, local Docker, macOS GUI, USB/WebAuthn on the laptop: ask the user. Do not freeze the container desk.

### Do it yourself (no kit)

**Shell:** packages, csv/xlsx, parse html, tar, disk, python http, pytest, public URL download, `doctor`.

**Desk, no secret:** open Chrome (`box-chrome`), dock click, fill a non-password field, scroll, GTK file chooser (1100×680), switch xfwm windows.

**Network, session already there:** read mail / search a connected service; draft, do not send.

Password-manager secrets you must not read, and video-as-pixels, are out of scope — ask, do not guess.

## Auth

`:7070` uses `GBM_CONTROL_TOKEN` (compose default `dev-local-token`). The CLI also reads the token from the container. `:1337` is `Bearer local` — only for `./scripts/gbm connect …`.

## Gotchas

- Idle has no Chrome; CDP empty until `box-chrome`.
- Pointer moves inside Xvfb, not on the Mac.
- `resize=off` on noVNC or coords drift.
- `/workspace` on the host is `./workspace`.
- Killing the CLI does not kill the desktop.
