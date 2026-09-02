---
name: gbm-grok-cu
description: >
  Grok-faithful computer use against grok-bot-machine via ./scripts/gbm connect
  (Connect-RPC :1337, 1280×800 PNG then xdotool). Use when the user wants the
  original grok screen loop, pixel clicks, vision computer-use, Connect-RPC,
  port 1337, or screenshot-then-click — not AT-SPI/CDP. Does not move the Mac cursor.
compatibility: Requires Docker, python3, and a running grok-bot container with :1337 published.
---

# gbm-grok-cu

Same loop as original grok-bot: read-only 1280×800 framebuffer, then `click` / `type` / `key` / `scroll` on that coordinate space. Clicks are OS-level on `DISPLAY=:1`, not Chrome CDP.

From the grok-bot-machine repo root:

```bash
./scripts/gbm ready
./scripts/gbm connect ping          # {}
./scripts/gbm connect caps          # computerUseSupported
./scripts/gbm connect screenshot -o workspace/cu.png
./scripts/gbm connect click 640 400
./scripts/gbm connect mouse 100 100
./scripts/gbm connect type 'hello'
./scripts/gbm connect key Return
./scripts/gbm connect exec '[{"scroll":{"direction":2,"amount":3}}]'
```

`--help` under `./scripts/gbm connect` is the flag source of truth.

## Coordinates

Origin **top-left**. x `0…1279`, y `0…799`. Do not change width while working. Open noVNC with `resize=off` if a human is watching.

Look at `workspace/cu.png` (or whatever `-o` you passed). Do **not** ingest raw base64 from JSON.

## Actions (`connect exec`)

JSON array of oneofs: `mouseMove`/`mouse_move`, `click`, `mouseDown`/`mouseUp`, `drag`, `scroll`, `type`, `key`, `wait`, `screenshot`, `cursorPosition`/`cursor_position`.

Click: `{"click":{"coordinate":{"x":640,"y":400},"button":1,"count":1}}`  
Button: 1 LEFT, 2 RIGHT, 3 MIDDLE. Scroll direction: 1 UP, 2 DOWN, 3 LEFT, 4 RIGHT.

Auth is `Bearer local` (CLI default). This is **not** `GBM_CONTROL_TOKEN`.

## Human handoff

If the PNG is a captcha, login, 2FA, or payment wall, do **not** click it. Switch to **gbm-desktop** and run `./scripts/gbm handoff --reason captcha` (or `login` / `2fa` / `payment`). Connect Exec is frozen until `./scripts/gbm resume`.

## When not to use this

Structured GTK/Chrome work belongs in **gbm-desktop** (`observe`, `a11y`, `cdp`, `shell`). Use this skill when the task is “see the pixels and click them.”
