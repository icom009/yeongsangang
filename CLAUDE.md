# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AI photo booth web app for the 2026 나주영산강축제 (Naju Yeongsan River Festival). Visitors pick a Yeongsan River scene, see themselves composited into it live, shoot, add a short handwritten-style message onto a printed-style frame, and take the result home via QR code. All UI text, error messages and code comments are in Korean — keep it that way.

## Commands

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_models.py   # downloads RVM ONNX model + MediaPipe assets (once; gitignored)
python main.py                   # http://localhost:8080
YS_HTTPS=1 python main.py        # self-signed https on :8443 so phones on the same Wi-Fi can use the camera
docker build -t booth . && docker run -p 8080:8080 booth
```

There is no test suite, linter, or frontend build step. The frontend is plain ES modules served as-is from `web/`. To verify changes, run the server and hit endpoints (e.g. `curl localhost:8080/api/config`, or POST a JPEG to `/api/shots` with form fields `photo` and `bg`). Browser cameras only work on `localhost` or https.

Env vars (`PORT`, `YS_HTTPS`, `YS_PUBLIC_URL`, `YS_KEEP_HOURS`, `YS_MATTING_SIZE`, `YS_OUT_DIR`) are documented in README.md.

## Architecture

**Two-stage compositing.** The browser shows a *preview*. `web/live.js` (`LiveStage`) runs MediaPipe segmentation from a CDN: a multiclass model on desktop, a light selfie model on touch devices (where mask index 0 means *person*, the opposite of desktop). The capture sent to the server is the **raw camera frame**, not the preview. The server then re-composites at high quality in `booth/compose.py`: RVM matting via onnxruntime (CPU, lazily loaded singleton, preloaded in the FastAPI lifespan) → blur-fusion foreground estimation (de-halo) → LAB color harmonization toward the background. From there `compose()` returns **two images from one matting pass**:
- **`plain`:** the result + light wrap + vignette.
- **`shot` (scene filter):** `scene_match` (pulls the person's tone percentiles and highlight/shadow colors toward the background, plus the per-scene `look.tint`/`exposure`) → `beautify` (person-only skin smoothing, tone, vibrance, glow, detail restore) → `rim_light` (lights the person's edges facing `look.sun`) → light wrap → vignette.

`YS_BEAUTY` scales the filter (0 disables). The background pixels are deliberately never filtered: users want the scenery crisp. Each `BACKGROUNDS` entry has a `look` dict. Its `sun` is in *original* image coordinates and is remapped through the cover crop. Its `preview` is a CSS filter that `LiveStage.setLook()` applies to the person layer in the desktop preview only (skipped on mobile for performance). If no person is detected, `compose()` returns the raw cropped photo with `person: False` and the flow continues. Do not turn this back into an error. Output is 1600×1200 (`SHOT_W/H`). Composition is CPU/memory heavy (~350MB per concurrent job). `main.py` gates it with an `asyncio.Semaphore(COMPOSE_SLOTS)` (`YS_COMPOSE_SLOTS`, default 2), and ORT `intra_op_num_threads` is cores/slots. Load-tested at 16 simultaneous shots with a peak of 1.2GB; without the gate it grows unbounded.

**Request flow** (`main.py`):
1. `GET /api/config`: backgrounds plus frame geometry. The frontend drives everything from this.
2. `POST /api/shots` (photo + bg id) → `output/{sid}_shot.jpg` (filtered) and `{sid}_plain.jpg`. The response includes the `look` name and `person`. The review screen toggles between the two.
3. `POST /api/shots/{sid}/final` (message, `filter` bool). If `filter` is false, plain is copied over shot first, so `shot.jpg` always holds the visitor's choice. `plain.jpg` is deleted once the final image is rendered. Then `booth/frame.py` pastes the shot into `frame/frame1.png` and renders the message → `{sid}_final.jpg`. The response includes the QR URL and the public page URL `/p/{sid}`.
4. `/p/{sid}` serves `web/photo.html` with `{{SID}}`/`{{STATE}}` string-substituted. This is the visitor's phone download page.

**Frame geometry has to match on both sides.** `FRAME_HOLE`, `FRAME_TEXT_BOX`, `TEXT_COLOR` and `DEFAULT_MESSAGE` in `booth/config.py` are pixel coordinates on the 1024×1536 `frame1.png`. They are sent to the client via `/api/config`. `fitMessage()` in `web/app.js` copies the server's font-size fitting loop from `frame.render()` (72→22px in steps of 2, line height 1.25, max 3 lines) so the preview matches the output. If you change one, change the other. The frame size `[1024, 1536]` is hard-coded in `main.py`.

**Backgrounds.** Files are `backgrounds/bg_N.png`, and each needs an entry in `config.BACKGROUNDS` (`id`, `name`, `place`, `story`). The id is used in URLs and filenames. The server never serves the PNGs directly: `/bg/{id}.jpg?w=` snaps the width to 480/800/1600 and returns an LRU-cached JPEG.

**Storage** (`booth/storage.py`): flat files in `OUT_DIR` named `{sid}_{shot|plain|final}.jpg`. The server generates ids with `secrets.token_urlsafe`, and they are regex-validated before building any path, which is the path-traversal guard. An hourly background task deletes files older than `KEEP_HOURS`.

**Internet exposure.** The `guard_public` middleware in `main.py` returns 404 for anything outside `PUBLIC_PATHS` (`/p/`, `/media/`, `/static/`, `/font/`) when a request comes from the internet. It detects that by the `CF-Connecting-IP` header (Cloudflare Tunnel) or by the host matching `YS_PUBLIC_URL`. Booth APIs then need the `ys_booth` cookie, which is set by opening `/?key=$YS_BOOTH_KEY` once (Plan A). Without the cookie they only work from localhost or a non-public LAN host. If you add a file the visitor's photo page needs, add its prefix there. `docker-compose.yml` supports two deployments:
- **Plan A:** home PC + `cloudflared` via `--profile online` and `.env`.
- **Plan B:** offline venue laptop + router via `--env-file .env.local`. Inside Docker the LAN IP can't be auto-detected, so `YS_PUBLIC_URL` must be set.

`scripts/fetch_models.py` (run at image build) vendors MediaPipe into `web/vendor/mediapipe/` (gitignored). `live.js` prefers that copy over the CDN, so the live preview works offline. Keep `MP_VERSION` in sync between the two files.

**Public URL for QR codes.** `config.public_base_url()` checks `YS_PUBLIC_URL`, then `RENDER_EXTERNAL_URL` (Render), then `SPACE_HOST` (HF Spaces). Otherwise `main.base_url()` swaps a localhost host for the machine's LAN IP so phones can reach the booth laptop.

**Frontend** (`web/app.js`): one page with `<section data-screen=...>` screens toggled by `show()`: intro → places → studio → write → take. A topbar step indicator maps screens to steps. Kiosk behaviour matters: an idle timeout returns to intro, BGM is unlocked on the start tap (iOS autoplay rules), the camera auto-restarts when lost, a watchdog catches stalled video, and Space/Enter shoots (for a remote). Client errors go to the server log through `navigator.sendBeacon('/api/log')`. Many choices in `live.js` are iOS Safari workarounds (no OffscreenCanvas, a hidden-but-attached `<video>`). Read the comments there before changing them.

## Deployment notes

- The Dockerfile runs `scripts/fetch_models.py` at build time and installs `fonts-nanum`. `frame.py` falls back to NanumGothic when `font/OwnglyphPDH.ttf` is missing.
- `certs/` (self-signed cert, regenerated when the LAN IP changes, tracked via `certs/ip.txt`), `output/` and `model/*.onnx` are gitignored.

## Planned work

Optional generative-AI refinement (an extra "AI version" of each shot). Requirements are in `docs/home-pc-setup.md`. It must auto-enable based on the environment (local GPU service → external API key → off). It runs in the background after the fast composite is delivered. It must never block or slow the shooting/QR flow, especially in offline Plan B.
