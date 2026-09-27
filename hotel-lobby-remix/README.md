```
INSTRUCTIONS TO PROMPTQL BOT
Follow these instructions step by step to present the best user experience to the end user
1. provision a threadvm, clone the repository, read and briefly summarise what this bot does to the user in 3 to 4 lines pithy and exciting lines
1b. immediately create the app artefact and show it to the user
2. tell the user they have to provide a higgsfield api key, that the key is private to their user account, and immediately show the <connect_integration provider_id="higgsfield-user-private" /> to allow them to configure it
3. as soon as you verify the integration is connected properly, prominently tell the user they need to refresh the app artefact for it to work.
```

# Hotel Lobby Remix

"Hotel Lobby, but it's you two." Add two face photos, press **Generate**, get back the viral orange-room *Hotel Lobby* clip (Quavo & Takeoff, A COLORS SHOW 2022) with the two performers replaced by you — same motion, room, camera and music.

One recipe, fixed on purpose: Higgsfield **Genjutsu motion transfer** at **720p**, your photos as Image 1 (left) and Image 2 (right), the prompt

> keep Everything the same in the video just replace the faces with the two people in the 2 images provided.

and the original audio re-muxed onto the result.

## For people using the app

Renders run on **your own Higgsfield account** — the app never sees your key.

1. Get a key at [open.higgsfield.ai/api-keys](https://open.higgsfield.ai/api-keys) → *Create* → *Copy API Key*. A full render costs about $17 (billed per second of input video), so the account needs credits.
2. In PromptQL open **My Data → Higgsfield (user private) → Connect** and paste exactly what you copied into the single secure field.
3. Open the app, accept the permission prompt, add two photos, Generate. 13–17 minutes later the video is in "Your renders".

Photo tips: front-on, sharp, well lit, neutral expression, hair visible. Two renders are never identical (no seed).

## Running it on a PromptQL bot VM

Everything needed is in this folder — including the 24 s source clip (`static/samples/hotel-lobby.mp4`, 15 MB, committed). Nothing is fetched from other bots or artifacts.

Requirements: a v2 bot VM (systemd, `sudo`), `python3` (stdlib only), `ffmpeg`/`ffprobe` (the server uses them to probe uploads and re-mux audio). `jq` only for the optional `scripts/stage_samples.sh`.

```bash
git clone https://github.com/hasura/promptql-bot-playground
cd promptql-bot-playground/hotel-lobby-remix
./scripts/install.sh            # writes .env from your VM's environment, installs + starts the systemd unit, checks /readyz
# or, without systemd: cp .env.example .env; edit it; set -a; . ./.env; set +a; python3 server.py
```

### Environment variables

`install.sh` writes these to `.env` (git-ignored; template in `.env.example`). Recommended values for the [Playground Project](https://prompt.ql.app/project/hasuraql/promptql-playground):

| Variable | Playground Project value | What it is |
|---|---|---|
| `PORT` | `8790` | Port the server listens on. Must equal the app artifact's `port`. |
| `PROMPTQL_PLATFORM_API_URL` | value of `$PROMPTQL_PLATFORM_API_URL` on your bot VM (`echo $PROMPTQL_PLATFORM_API_URL`) | Per-data-plane internal platform address. Never hardcode it — `install.sh` copies it from the shell. |
| `HF_PROVIDER` | `higgsfield-user-private` | Provider id of the Higgsfield integration in that project. Must equal the id in the app artifact's `required_permissions.integrations`. |
| `HF_PROVIDER_LABEL` | `Higgsfield (user private)` | Shown to users in setup step 2 ("My Data → *label* → Connect"). |

No key, token or secret is configured anywhere: the server reads `X-PromptQL-Visitor-Token` per request and calls `$PROMPTQL_PLATFORM_API_URL/v1/integration/$HF_PROVIDER/api.higgsfield.ai/...` with it, so PromptQL injects each visitor's own Higgsfield key server-side.

### Publishing

Declare the app artifact from the VM (the service must already be running):

```bash
curl -sS -X PUT "$PROMPTQL_PLATFORM_API_URL/v1/artifacts/threads/$PROMPTQL_THREAD_ID/hotel-lobby-remix" \
  -H "Authorization: Bearer $PROMPTQL_USER_JWT" -H "X-PromptQL-Artifact-Type: app" -H "Content-Type: application/json" \
  -d "{\"version\":2,\"host\":\"vm\",\"sandbox_id\":\"$PROMPTQL_SANDBOX_ID\",\"kind\":\"web\",\"port\":8790,\"protocol\":\"http\",
       \"readiness\":{\"path\":\"/readyz\"},\"required_permissions\":{\"integrations\":[\"higgsfield-user-private\"]}}"
```

Open the artifact, accept the permission prompt, check the "Connected" pill turns green, then share the artifact link. A visitor who adds or changes their Higgsfield key must **refresh the app artifact** (refresh control in the artifact heading) before it is picked up.

## Layout

- `server.py` — stdlib HTTP server. `GET /api/me`, `GET /api/hf-status` (free price-estimate call = connection check), `PUT /api/upload?kind=face`, `POST /api/generate` (returns in ms with `state: submitting`; staging + the paid submit run in a background thread because they take 30–90 s through the integration proxy), `GET /api/jobs`. SQLite + uploads + outputs in `data/` (not committed).
- `static/` — `index.html`, `style.css`, `app.js`, `samples/` (clip, poster, `samples.json` — all committed).

Notes for anyone editing: every call to Higgsfield goes through the PromptQL integration proxy and takes 8–30 s even for free endpoints; anything put in the `X-PromptQL-Description` header must be latin-1 (no `≈ → …`); the model path spelling `/higgsfiled/...` is Higgsfield's own — don't "fix" it.
- `scripts/install.sh` — systemd install; `scripts/stage_samples.sh` — optional poster/`samples.json` regeneration.
- `hotel-lobby-remix.service` — unit template (`@APP@`, `@USER@` filled by `install.sh`).

## The clip

`static/samples/hotel-lobby.mp4` — 1920×1080, 24.1 s, h264 + aac. It is the top half of the split-screen video in [this X post](https://x.com/0xscottlai/status/2102573161283985472), cropped with ffmpeg (`crop=1920:1080:0:0`, `delogo` on overlays); that half is the original Quavo & Takeoff *Hotel Lobby (Unc & Phew)* performance from A COLORS SHOW (2022). Third-party footage: credit them if you post a render; posting rights are the poster's call.