# F1 Battle Detector

Real-time Formula 1 battle detection and tracking system. A live race companion that identifies and ranks the most exciting on-track battles using OpenF1 API data.

## Features

- ⚡ Real-time battle detection between adjacent drivers
- 🎯 Scoring on gap, closing rate and pace advantage, with a stability filter
- 🚦 Safety car and pit stop awareness
- 📊 Gap trend sparklines
- 📱 Mobile-first responsive design
- 🔴 Live connection status indicator
- 🧪 `DATA_MODE=mock` for developing without a live session

## Architecture

**Backend**: Python + FastAPI

- Polls OpenF1 API every 1-2 seconds for positions
- Maintains in-memory driver state with rolling history
- Detects and scores battles with context awareness
- Exposes clean REST API

**Frontend**: Next.js + React + TypeScript

- Real-time dashboard with SWR auto-refresh
- Modern UI with TailwindCSS
- Connection status monitoring

**Data Source**: [OpenF1 API](https://openf1.org) (free, no auth required)

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker (optional, for containerized deployment)

## Quick Start (Development)

### Option 1: Docker Compose (Recommended)

```bash
# Clone the repository
git clone <your-repo-url>
cd F1-Battle-Detector-Summarizer

# Copy environment files
cp backend/.env.example backend/.env
cp frontend/.env.local.example frontend/.env.local

# Start services
docker-compose up --build

# Backend: http://localhost:8000
# Frontend: http://localhost:3000
# API Docs: http://localhost:8000/docs
```

### Option 2: Manual Setup

**Backend:**

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy .env file
cp .env.example .env

# Run server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Frontend:**

```bash
cd frontend

# Install dependencies
npm install

# Copy .env file
cp .env.local.example .env.local

# Run dev server
npm run dev
```

## Project Structure

```bash
F1-Battle-Detector-Summarizer/
├── .github/workflows/ci.yml  # Backend tests, frontend lint and build
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app, polling loops, endpoints
│   │   ├── openf1_client.py  # OpenF1 API wrapper
│   │   ├── models.py         # Pydantic data models
│   │   ├── state.py          # In-memory state manager
│   │   ├── battle.py         # Battle detection and scoring
│   │   ├── session.py        # Session lifecycle
│   │   ├── health.py         # Health monitoring
│   │   ├── mock_data.py      # DATA_MODE=mock data generator
│   │   ├── demo.py           # Mock mode without a poll loop (serverless)
│   │   └── config.py         # Configuration
│   ├── api/index.py          # Vercel entrypoint (ASGI)
│   ├── vercel.json
│   ├── tests/                # Unit and integration tests
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── Dockerfile
├── frontend/
│   ├── app/
│   │   ├── components/       # React components
│   │   ├── hooks/            # Custom SWR hooks
│   │   ├── lib/              # API client
│   │   ├── types/            # TypeScript types
│   │   └── page.tsx          # Main dashboard
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── docs/DEPLOY-VERCEL.md     # Deploying both halves to Vercel
├── PLAN.md                   # Detailed implementation plan
└── README.md
```

## API Endpoints

### Session

- `GET /session/current` - Get current active session

### Live Data

- `GET /state/latest` - All 20 drivers' current state
- `GET /battles/top?k=5` - Top K battles ranked by score
- `GET /drivers/{driver_number}/trend?points=10` - Gap trend for a driver

### System

- `GET /health` - Health check with detailed diagnostics

## Testing

**Backend:**

```bash
cd backend
pytest tests/ -v
```

**Frontend:**

There are no frontend tests yet. `npm run lint` and `npm run build` are what CI
runs, and the build type-checks the whole app:

```bash
cd frontend
npm run lint
npm run build
```

(`npm test` is declared in `package.json` but points at a jest that is not
installed.)

## Deployment

### Vercel (both halves, mock data on repeat)

Two Vercel projects from this repo - `backend/` and `frontend/` - running the
scripted 24-lap mock race on a six-minute loop. No OpenF1 token, no always-on
process, $0/month. Step by step in
[docs/DEPLOY-VERCEL.md](./docs/DEPLOY-VERCEL.md).

The one thing to know: Vercel freezes the process between requests, so the
background poll loop that normally fills the pipeline never advances there.
`DEMO_STATELESS=true` (set automatically under Vercel) makes each request
rebuild the last 48 ticks of the race into a throwaway pipeline instead - same
detection code, ~20ms, no process to keep alive. See `backend/app/demo.py`.

### Live data

`DATA_MODE=live` needs a process that stays alive to poll, which serverless is
not. Use the `docker-compose.yml` here, or a container host such as
[Render](https://render.com), with Vercel serving the frontend against it.

## Configuration

Key environment variables (see `.env.example` files):

**Backend:**

- `OPENF1_BASE_URL` - OpenF1 API endpoint
- `POLL_POSITIONS_INTERVAL_S` - Position polling frequency (default: 1.5s)
- `BATTLE_WATCH_SCORE` - Threshold for "WATCH" battles (default: 0.55)
- `BATTLE_HOT_SCORE` - Threshold for "HOT" battles (default: 0.70)
- `DATA_MODE` - Where driver data comes from: `live`, `replay` or `mock` (default: `mock`)
  - `mock` - generated data, never touches the network. What CI runs.
  - `live` - OpenF1 live timing; needs `OPENF1_API_TOKEN` (paid Sponsor tier)
  - `replay` - a cached historical race (Phase 1, not yet implemented)
- `OPENF1_API_TOKEN` - Sponsor-tier token, required only for `DATA_MODE=live`
- `TEST_MODE` - Deprecated alias for `DATA_MODE`. Honoured only when `DATA_MODE`
  is unset (`true` maps to `mock`, `false` to `live`) and warns at startup.
- `DEMO_STATELESS` - Serve mock data per request instead of from a background
  poll loop. Defaults on when `VERCEL` is set, off everywhere else.
- `DEMO_WINDOW_TICKS` - Ticks of mock race replayed per request (default: 48)
- `CORS_ORIGIN_REGEX` - Origin pattern to allow alongside `CORS_ORIGINS`, for
  preview deployments whose hostname changes on every push

`backend/.env` is read on startup. Real environment variables take precedence,
so `DATA_MODE=mock uvicorn app.main:app` overrides the file.

**Frontend:**

- `NEXT_PUBLIC_API_BASE_URL` - Backend API URL

## Development Status

Working end to end. The backend polls OpenF1, detects and scores battles, and
serves them over REST; the frontend renders them live.

- ✅ Battle detection and scoring
- ✅ OpenF1 polling loops (positions, intervals, laps, session)
- ✅ Frontend dashboard with live refresh
- ✅ Docker setup and CI
- ✅ 121 backend tests

Known limits:

- Opening laps under-detect. Before any driver completes a lap there is no pace
  delta, which caps the score below the `WATCH` threshold.
- No frontend tests. `next build` type-checks the app in CI; that is all.
- Blue flag situations are not detected. Battles are paired by adjacent
  classified position, and lapped cars are never adjacent to the car lapping
  them, so it would need pairing by track position.

See [PLAN.md](./PLAN.md) for the original roadmap.

## Contributing

This is a personal project, but suggestions and feedback are welcome! Open an issue to discuss major changes.

## License

MIT

## Acknowledgments

- [OpenF1](https://openf1.org) for the amazing free F1 data API
- Formula 1 community for inspiration

---

**Note**: PLAN.md is the original design document and describes some features that
were never built. This README describes what the code actually does.
