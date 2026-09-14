# Deploying to Vercel

Two Vercel projects from this one repo: `backend/` (FastAPI, Python functions)
and `frontend/` (Next.js). They deploy independently and talk over HTTPS.

The deployment runs in **mock mode on repeat** — a scripted 24-lap race that
restarts every six minutes, with no OpenF1 calls and no API token.

## Why two projects

Vercel's Next.js builder wants the Next app at the project root, and Python
functions are bundled from the project root too. One project cannot have both
roots. Two projects, each with its own Root Directory, is the arrangement that
works without fighting the platform.

## Why the backend needed a code change

The backend normally polls on a background asyncio loop and accumulates driver
history and tracked battles in memory between polls. Vercel freezes the process
the moment a response is sent, so that loop gets a few hundred milliseconds per
request and then stops — every endpoint would report "no driver data
available", forever.

`DEMO_STATELESS=true` (set in `backend/vercel.json`, and implied by Vercel's own
`VERCEL=1`) switches to rebuilding instead of accumulating: each request works
out which tick of the race the clock is on, replays the previous 48 ticks into a
throwaway pipeline, and serves that. Detection, closing rate, the stability
filter and eviction all run exactly as they do live. It costs ~20ms per request.
See `backend/app/demo.py`.

Nothing changes for Docker or `uvicorn` — they keep the long-running loop.

## Deploy

Install and log in once:

```bash
npm i -g vercel
vercel login
```

### 1. Backend

```bash
cd backend
vercel --prod
```

Answer the prompts: link to a new project, name it something like
`f1-battle-api`, accept `./` as the root directory, and take the detected
settings (there is no framework to detect — `vercel.json` handles the routing).

No environment variables to set: `backend/vercel.json` pins `DATA_MODE=mock`,
`DEMO_STATELESS=true`, and a CORS pattern that allows any `*.vercel.app` origin,
which is what makes preview deployments work without re-listing hostnames.

Copy the production URL it prints, then check it:

```bash
curl https://<backend-url>/health
curl "https://<backend-url>/battles/top?k=5"
```

`/health` should say `healthy` and `/battles/top` should return battles. If it
returns an empty list, wait a few seconds and retry — the scripted race has
short quiet phases of up to ~5 seconds.

### 2. Frontend

`NEXT_PUBLIC_API_BASE_URL` is inlined at build time, so it has to be set
*before* the build, for every environment you intend to use:

```bash
cd ../frontend
vercel link                     # new project, e.g. f1-battle-detector
printf 'https://<backend-url>' | vercel env add NEXT_PUBLIC_API_BASE_URL production
printf 'https://<backend-url>' | vercel env add NEXT_PUBLIC_API_BASE_URL preview
vercel --prod
```

Open the URL. Within a few seconds the board should fill with battles and the
connection indicator should read live.

## Push-to-deploy

To have both redeploy on every push instead of running the CLI, import the repo
twice in the Vercel dashboard (Add New → Project → same repo) and set **Root
Directory** to `backend` on one and `frontend` on the other. Everything else
carries over from the files in this repo; re-add `NEXT_PUBLIC_API_BASE_URL` to
the frontend project.

## Tuning the demo

Both live in `backend/app/mock_data.py`:

- `TOTAL_LAPS` / `TICKS_PER_LAP` set the loop length. 24 laps at 10 ticks a lap
  and a 1.5s tick is a six-minute race.
- The scripted gaps in `generate_driver_states` are periodic functions of
  `phase`, so the loop closes seamlessly. Their quiet phases are deliberately
  spread apart; `tests/test_demo.py` fails if the board would sit empty for more
  than 20 seconds, or if the race stops reaching `HOT`.

`DEMO_WINDOW_TICKS` (default 48) trades per-request cost against how much
history the sparklines show. Below `BATTLE_GAP_TREND_WINDOW * 3` no battle
accumulates enough distinct gap samples to be shown at all.

## If the build fails

- **`pydantic-core` or `httptools` fails to build a wheel** — Vercel's default
  Python is newer than the 3.11 in `backend/Dockerfile`. Pin the dependency
  versions in `backend/requirements.txt` up to versions with wheels for that
  Python.
- **Frontend builds but shows no data** — `NEXT_PUBLIC_API_BASE_URL` was unset
  or set after the build. Check the browser console for a request to
  `localhost:8000`, then re-add the variable and redeploy.
- **Requests blocked by CORS** — the frontend is on a custom domain rather than
  `*.vercel.app`. Add it to `CORS_ORIGINS` in `backend/vercel.json`.

## Going live later

`DATA_MODE=live` needs an OpenF1 Sponsor-tier token and, more importantly, a
process that stays alive to poll — which serverless is not. Live mode wants a
container host (the `docker-compose.yml` in this repo, or Fly/Railway/Render),
with Vercel serving the frontend against it.
