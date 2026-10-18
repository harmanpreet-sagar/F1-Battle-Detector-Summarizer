# Deploying to Vercel

Two Vercel projects from this one repo: `backend/` (FastAPI, Python functions)
and `frontend/` (Next.js). They deploy independently and talk over HTTPS.

The deployment is a **replay broadcast**: six real 2025 races, cached from
OpenF1 and shipped inside the backend function, played one after another at 5x
on a loop. There are no OpenF1 calls at runtime and no API token.

(Docker instead? The root `Dockerfile` builds the same app as one same-origin
container; see the README.)

## Why two projects

Vercel's Next.js builder wants the Next app at the project root, and Python
functions are bundled from the project root too. One project cannot have both
roots. Two projects, each with its own Root Directory, is the arrangement that
works without fighting the platform.

## Why the backend rebuilds on every request

The backend normally runs a background loop that feeds a long-lived pipeline.
Vercel freezes the process the moment a response is sent, so that loop would
never advance.

`DEMO_STATELESS=true` (set in `backend/vercel.json`, and implied by Vercel's own
`VERCEL=1`) rebuilds instead. Where the broadcast is depends only on the wall
clock (`REPLAY_EPOCH`, `REPLAY_SPEED`), so each request works out the race and
race time, replays the ticks just before it into a throwaway pipeline (60 to
fill driver history, then 48 detected), and serves that. Every viewer and every
instance sees the same moment. Across a full race the rebuild reports the same
battles as a server that has run since lights out; see
`backend/app/replay/broadcast.py`.

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

No environment variables to set. `backend/vercel.json` pins `DATA_MODE=replay`,
`DEMO_STATELESS=true` and a CORS pattern that allows any `*.vercel.app` origin
(which is what makes preview deployments work without re-listing hostnames). Its
`includeFiles` ships `data/sessions/**` with the function, and `.vercelignore`
keeps everything else under `data/` out.

Copy the production URL it prints, then check it:

```bash
curl https://<backend-url>/api/health
curl https://<backend-url>/api/replay/status
curl "https://<backend-url>/api/battles/top?k=5"
```

`/api/health` should say `healthy` with `"mode": "replay"`, `/api/replay/status`
should name a 2025 Grand Prix and a lap, and `/api/battles/top` should usually
return battles. An empty list is normal for a few seconds at a time, and for
longer under a safety car.

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

Open the URL. The header should read REPLAY with the race and lap, and battles
should appear within a few seconds.

## Push-to-deploy

To have both redeploy on every push instead of running the CLI, import the repo
twice in the Vercel dashboard (Add New → Project → same repo) and set **Root
Directory** to `backend` on one and `frontend` on the other. Everything else
carries over from the files in this repo; re-add `NEXT_PUBLIC_API_BASE_URL` to
the frontend project.

Deploy both from the same commit. Every route moved under `/api` in 1.5, so a
frontend built before that cannot talk to a backend built after it.

## Tuning the broadcast

Set these as environment variables on the backend project (or in
`backend/vercel.json`):

- `REPLAY_SPEED` (default 5): race seconds per wall second. At 5x a race takes
  about 19 minutes and the six-race loop about two hours.
- `REPLAY_PLAYLIST`: comma-separated session keys, in order. Unset plays every
  race in `backend/data/sessions/`, in calendar order.
- `REPLAY_EPOCH`: the instant the loop started. Changing it moves every viewer
  to a different point in the loop.

To add a race, cache it locally (`python -m app.ingest --year 2025 --round N`),
commit `backend/data/sessions/<key>/`, and redeploy. `tests/test_broadcast.py`
checks that every committed race loads, has a name and lap count, and is not
stuck under a safety car.

## Measurements

On the PR #14 preview (function in `iad1`, measured from Toronto through the
`yul1` edge, 20 requests):

| | |
| --- | --- |
| Warm `/api/battles/top` | 151 ms median, 131-238 ms, after a 72 ms TLS handshake |
| A trivial route through the same function | ~145 ms - so nearly all of a warm request is network and routing, not the rebuild |
| A request landing on a new instance | ~1.8 s, one in the first eight |

Every response carries a `Server-Timing: app;dur=…` header with the app's own
time, and the first request on each instance adds `first`, so the server-side
cost can be read off a deployment directly:

```bash
curl -sI https://<backend-url>/api/battles/top | grep -i server-timing
```

Read that way on the next preview (11 warm requests): the app spends **70-81
ms** per `/api/battles/top`, against ~30 ms locally, while the request as a
whole took 250-750 ms. The rebuild is a fifth to a third of what a viewer
waits; the rest is Vercel. A cold sample is still to take - the first
request to that deployment came from somewhere else.

Measured locally on Windows:

| | |
| --- | --- |
| Race data shipped | 2.2 MB for six races |
| Python dependencies | ~20 MB installed |
| Cold start | ~1.2 s import (mostly FastAPI) + ~1.1 s first request, which indexes all six races |
| Warm request | ~30 ms (rebuild of 108 ticks) |

The function is roughly 25 MB against Vercel's 250 MB limit, so bundle size is
not a constraint. If the cold start shows up on a preview deployment, the
first-request indexing is the part to cut: store each race's start, end and
stoppages in its manifest at ingest, and build a race's full timeline only when
the broadcast reaches it.

## Deployment log

- **1.7: replay replaces mock.** Two problems were found before deploying, both
  while choosing the playlist. OpenF1 rate-limited a fast ingest (HTTP 429), and
  the client retried with no delay at all; it now backs off, doubling from
  `OPENF1_RETRY_BASE_S`, and honours `Retry-After`. And steward notices that
  mention the safety car ("... NOTED - SAFETY CAR INFRINGEMENT") were read as
  deployments, which froze the 2025 Bahrain GP under a safety car for its last
  27 minutes; only race control's `SafetyCar` category deploys one now.
- **1.8: the Belgian GP's safety car start.** Its first four laps behind the
  safety car were shown as racing, with battles, because race control announces
  a safety car start and a rolling start under `Other` and never says when the
  racing begins. Those messages are read now, and a rolling start ends when the
  leader completes the lap. A test replays every shipped race through the
  detector and fails on any battle shown under a neutralised track.

## If the build fails

- **`pydantic-core` or `httptools` fails to build a wheel** — Vercel's default
  Python is newer than the 3.11 in the root `Dockerfile`. Pin the dependency
  versions in `backend/requirements.txt` up to versions with wheels for that
  Python.
- **`/api/health` works but every replay endpoint returns 500** — the races did
  not ship. Check that `backend/data/sessions/` is committed and that the
  function's file listing in the deployment includes it.
- **Frontend builds but shows no data** — `NEXT_PUBLIC_API_BASE_URL` was unset
  or set after the build. Unset, the page calls `/api` on its own domain, which
  404s on Vercel. Check the browser console, re-add the variable and redeploy.
- **Requests blocked by CORS** — the frontend is on a custom domain rather than
  `*.vercel.app`. Add it to `CORS_ORIGINS` in `backend/vercel.json`.

## Going live later

`DATA_MODE=live` needs an OpenF1 Sponsor-tier token and, more importantly, a
process that stays alive to poll — which serverless is not. Live mode wants a
container host (the root `Dockerfile`, or Fly/Railway/Render), with Vercel
serving the frontend against it.
