# F1 Battle Detector

Real-time Formula 1 battle detection and tracking system. A live race companion that identifies and ranks the most exciting on-track battles using OpenF1 API data.

## Features

- ⚡ Real-time battle detection between adjacent drivers
- 🎯 Smart scoring algorithm (gap, closing rate, pace advantage)
- 🚦 Safety car and pit stop awareness
- 📊 Gap trend visualizations (sparklines)
- 📱 Mobile-first responsive design
- 🔴 Live connection status indicator
- 🏎️ Driver watchlist (coming soon)

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
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI application
│   │   ├── openf1_client.py  # OpenF1 API wrapper
│   │   ├── models.py         # Pydantic data models
│   │   ├── state.py          # In-memory state manager
│   │   ├── battle.py         # Battle detection logic
│   │   ├── session.py        # Session management
│   │   ├── health.py         # Health monitoring
│   │   └── config.py         # Configuration
│   ├── tests/                # Unit and integration tests
│   ├── requirements.txt
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
├── PLAN.md                   # Detailed implementation plan
└── README.md
```

## API Endpoints

### Session

- `GET /session/current` - Get current active session

### Live Data

- `GET /state/latest` - All 20 drivers' current state
- `GET /battles/top?k=5` - Top K battles ranked by score
- `GET /drivers/{driver_number}/trend` - Gap trend for specific driver

### System

- `GET /health` - Health check with detailed diagnostics

## Testing

**Backend:**

```bash
cd backend
pytest tests/ -v
```

**Frontend:**

```bash
cd frontend
npm test
```

## Deployment (FREE Options)

See [PLAN.md](./PLAN.md) for detailed deployment instructions.

**Recommended Stack:**

- Backend: [Render.com](https://render.com) (750 hours/month free)
- Frontend: [Vercel](https://vercel.com) (unlimited hobby projects)
- Total cost: **$0/month**

## Configuration

Key environment variables (see `.env.example` files):

**Backend:**

- `OPENF1_BASE_URL` - OpenF1 API endpoint
- `POLL_POSITIONS_INTERVAL_S` - Position polling frequency (default: 1.5s)
- `BATTLE_WATCH_SCORE` - Threshold for "WATCH" battles (default: 0.55)
- `BATTLE_HOT_SCORE` - Threshold for "HOT" battles (default: 0.70)

**Frontend:**

- `NEXT_PUBLIC_API_BASE_URL` - Backend API URL

## Development Status

This is a starter skeleton with:

- ✅ Complete project structure
- ✅ Configuration and package files
- ✅ Stub implementations with clear TODOs
- ✅ Docker setup for local development
- 🚧 Battle detection algorithm (in progress)
- 🚧 OpenF1 polling loops (in progress)
- 🚧 Frontend components (in progress)

See [PLAN.md](./PLAN.md) for the full implementation roadmap.

## Contributing

This is a personal project, but suggestions and feedback are welcome! Open an issue to discuss major changes.

## License

MIT

## Acknowledgments

- [OpenF1](https://openf1.org) for the amazing free F1 data API
- Formula 1 community for inspiration

---

**Note**: This app is in active development. Some features mentioned in PLAN.md are not yet implemented. Check the TODOs in the code for current status.
