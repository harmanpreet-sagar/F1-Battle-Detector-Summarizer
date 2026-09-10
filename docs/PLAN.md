# F1 Battle Detector - Enhanced Implementation Plan

## Architecture Overview

**Backend (Python/FastAPI)** - Real-time data processing with resilience

- Polls OpenF1 API with exponential backoff on failures
- Maintains in-memory state + rolling history per driver
- Detects and filters battles based on session conditions (green flag, pit stops, safety cars)
- Exposes REST API with health indicators
- **Cost**: $0 (FastAPI is open source)

**Frontend (Next.js/React)** - Live companion dashboard

- Real-time battle updates with connection status
- Mobile-first responsive design
- Graceful degradation when data is stale
- Driver watchlist and trend visualizations
- **Cost**: $0 (React/Next.js are open source)

**Data Persistence (SQLite)** - Lightweight session storage

- Snapshots every 30s for replay/debugging
- No complex setup needed for MVP
- **Cost**: $0 (SQLite is public domain, just a file)

**Data Source (OpenF1 API)** - Community-maintained F1 data

- Real-time telemetry, positions, lap times
- No authentication required
- **Cost**: $0 (free community API)

**Total Monthly Cost**: $0 (100% free stack)

---

## Repository Structure

```bash
f1-battle-detector/
  backend/
    app/
      main.py                 # FastAPI app + background tasks
      openf1_client.py        # API wrapper with retry logic
      models.py               # Pydantic models
      state.py                # In-memory state manager
      battle.py               # Battle detection & scoring
      session.py              # Session lifecycle management
      health.py               # Health checks & status
      config.py               # Configuration from env vars
    tests/
      test_battle.py          # Unit tests for scoring
      test_openf1_client.py   # Mocked API tests
      fixtures/
        sample_positions.json
        sample_laps.json
    requirements.txt
    requirements-dev.txt      # pytest, httpx
    Dockerfile
    .env.example
  frontend/
    app/
      page.tsx                # Main dashboard
      layout.tsx
      components/
        ConnectionStatus.tsx  # Live/Delayed/Offline indicator
        BattleCard.tsx
        BattlesList.tsx
        Sparkline.tsx
        Watchlist.tsx
        DriverCard.tsx
      hooks/
        useBattles.ts         # SWR hook for /battles/top
        useSession.ts         # SWR hook for /session/current
      lib/
        api-client.ts
      types/
        api.ts
    package.json
    next.config.js
    tailwind.config.js
    Dockerfile
  docker-compose.yml
  .env.example
  README.md
```

---

## Backend Implementation Details

### Data Models

**DriverState**

```python
driver_number: int
full_name: str
team_name: str
position: int
last_lap_time_s: float | None
gap_to_leader_s: float | None
gap_to_ahead_s: float | None
tire_compound: str | None
tire_age_laps: int | None
pit_stops_count: int
updated_at: datetime
data_confidence: Literal["high", "medium", "low"]
```

**Battle**

```python
battle_id: str                  # "{chaser}_{ahead}"
chaser_driver_number: int
ahead_driver_number: int
chaser_position: int
ahead_position: int
gap_now_s: float
closing_rate_s_per_s: float | None
pace_delta_s_per_lap: float | None
battle_score: float
intensity: Literal["HOT", "WATCH", "NONE"]
explanation: str
trend_gap_s: list[float]        # Last 6 updates
trend_timestamps: list[datetime]
duration_updates: int           # How long battle has existed
flags: BattleFlags              # See below
```

**BattleFlags** (new)

```python
pit_window_active: bool         # Sudden gap change detected
under_yellow: bool              # Safety car/VSC active
blue_flag_situation: bool       # Lapping scenario
data_quality_warning: bool      # Missing/stale data
```

**SessionStatus**

```python
session_key: int
session_name: str               # "Race", "Qualifying", etc.
session_type: str
session_status: Literal["started", "finished", "aborted"]
circuit_short_name: str
meeting_name: str
current_lap: int | None
total_laps: int | None
track_status: Literal["green", "yellow", "red", "sc", "vsc"] | None
gmt_offset: str
updated_at: datetime
```

**HealthStatus**

```python
status: Literal["healthy", "degraded", "unhealthy"]
openf1_connected: bool
last_successful_poll_positions: datetime
last_successful_poll_laps: datetime
data_delay_seconds: float
active_session: bool
error_count_last_minute: int
message: str | None
```

### API Endpoints

**Session Management**

- `GET /session/current` - Current session info + track status
- `GET /session/list?date=2026-03-15` - Available sessions (for replay mode later)

**Live Data**

- `GET /state/latest` - All 20 drivers' current state
- `GET /battles/top?k=5&min_intensity=WATCH` - Top battles filtered by intensity
- `GET /drivers/{driver_number}/trend?points=10` - Gap trend for specific driver

**System**

- `GET /health` - Health check with detailed status
- `GET /metrics` - API call counts, error rates (optional)

### Configuration (`config.py`)

Load from environment variables with sensible defaults:

```python
# OpenF1 API
OPENF1_BASE_URL = "https://api.openf1.org/v1"
OPENF1_TIMEOUT_S = 5.0
OPENF1_MAX_RETRIES = 3

# Polling intervals
POLL_POSITIONS_INTERVAL_S = 1.5
POLL_LAPS_INTERVAL_S = 10.0
POLL_SESSION_INTERVAL_S = 30.0

# Battle detection
BATTLE_GAP_TREND_WINDOW = 6         # Updates
BATTLE_PACE_TREND_WINDOW = 3        # Laps
BATTLE_MIN_DURATION_UPDATES = 3     # Stability filter
BATTLE_MAX_GAP_S = 3.0

# Thresholds
BATTLE_WATCH_SCORE = 0.55
BATTLE_WATCH_GAP_S = 1.8
BATTLE_HOT_SCORE = 0.70
BATTLE_HOT_GAP_S = 1.2

# Data quality
DATA_STALE_THRESHOLD_S = 5.0
DATA_CONFIDENCE_MEDIUM_S = 3.0

# CORS
CORS_ORIGINS = ["http://localhost:3000", "https://yourdomain.com"]

# Storage (optional for MVP)
SQLITE_DB_PATH = "./data/f1_sessions.db"
SNAPSHOT_INTERVAL_S = 30.0
```

### Polling Strategy with Error Handling

**Position Polling Loop** (`poll_positions()`)

```python
async def poll_positions():
    retry_delay = 1.0
    max_retry_delay = 30.0
    
    while True:
        try:
            session = await get_current_session()
            if not session:
                await asyncio.sleep(30)
                continue
            
            positions = await openf1_client.get_positions(session.session_key)
            
            # Update state manager
            state_manager.update_positions(positions)
            
            # Detect sudden gap changes (pit stops)
            state_manager.detect_pit_windows()
            
            # Reset retry delay on success
            retry_delay = config.POLL_POSITIONS_INTERVAL_S
            
            await asyncio.sleep(config.POLL_POSITIONS_INTERVAL_S)
            
        except OpenF1APIError as e:
            logger.error(f"OpenF1 API error: {e}")
            health_manager.record_error()
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
        except Exception as e:
            logger.exception("Unexpected error in position polling")
            await asyncio.sleep(retry_delay)
```

**Session Status Polling** (new)

- Poll every 30s to detect track status changes (green → yellow flag)
- Updates `SessionStatus.track_status`
- Used to filter battles during safety car periods

### Battle Detection Algorithm

**Core Logic** (`battle.py`)

1. **Only compute battles between adjacent positions** (P2↔P1, P3↔P2, etc.)
2. **Pre-filtering checks**:

- Gap must be < 3.0s
- Both drivers must have recent data (< 5s old)
- Skip if blue flag situation (position difference > 5)
- Downweight score by 50% if under yellow/SC

1. **Scoring function**:

```python
def calculate_battle_score(
    gap_s: float,
    closing_rate: float | None,
    pace_delta: float | None,
    flags: BattleFlags
) -> float:
    # Base score from gap (0-1, smaller = higher)
    gap_score = max(0, 1 - (gap_s / 3.0))
    
    # Closing rate contribution (0-1)
    closing_score = 0.0
    if closing_rate is not None:
        closing_score = min(1.0, max(0, closing_rate * 10))
    
    # Pace advantage contribution (0-1)
    pace_score = 0.0
    if pace_delta is not None and pace_delta < 0:
        # Negative = chaser is faster
        pace_score = min(1.0, abs(pace_delta) * 2)
    
    # Weighted combination
    raw_score = (gap_score * 0.5) + (closing_score * 0.3) + (pace_score * 0.2)
    
    # Apply penalties
    if flags.under_yellow:
        raw_score *= 0.5
    if flags.pit_window_active:
        raw_score *= 0.3
    if flags.data_quality_warning:
        raw_score *= 0.7
    
    return raw_score
```

1. **Intensity labeling**:

```python
if score > 0.70 and gap < 1.2 and closing_rate > 0:
    intensity = "HOT"
elif score > 0.55 and gap < 1.8:
    intensity = "WATCH"
else:
    intensity = "NONE"  # Don't show
```

1. **Stability filter**:

- Track `duration_updates` for each battle
- Only return battles with `duration_updates >= 3`
- Prevents flickering on/off in UI

1. **Pit stop detection**:

```python
def detect_pit_window(driver_history: list[DriverState]) -> bool:
    if len(driver_history) < 2:
        return False
    
    gap_change = abs(driver_history[-1].gap_to_ahead_s - 
                     driver_history[-2].gap_to_ahead_s)
    
    # If gap changed by > 10s in one update, likely pit stop
    return gap_change > 10.0
```

---

## Frontend Implementation Details

### Component Hierarchy

```bash
page.tsx
├── ConnectionStatus (top banner)
├── SessionHeader (race name, lap, countdown)
├── BattlesList
│   └── BattleCard × N
│       ├── DriverPair (names, positions, teams)
│       ├── GapInfo (current gap, closing rate)
│       ├── IntensityBadge (HOT/WATCH)
│       └── Sparkline (gap trend)
└── Watchlist (sidebar)
    └── DriverCard × 3
        └── NearestBattle info
```

### Key Components

**ConnectionStatus.tsx** (critical for live use)

```typescript
// Shows data freshness
type Status = "live" | "delayed" | "reconnecting"

// Appears as top banner:
// 🟢 LIVE (1.2s delay)
// 🟡 DELAYED (8.5s delay)  
// 🔴 RECONNECTING...
```

**BattleCard.tsx**

```typescript
interface BattleCardProps {
  battle: Battle
  showSparkline?: boolean
}

// Visual hierarchy:
// - Large: Position numbers (P3 → P2)
// - Medium: Driver names + team colors
// - Small: Gap (0.87s) • Closing (0.12s/s) • Pace advantage
// - Badge: HOT (red) or WATCH (orange)
// - Graph: Tiny sparkline of last 6 gap values
```

**Sparkline.tsx**

```typescript
// Minimal line chart using SVG or recharts
// Max height: 40px
// Shows last 6 data points
// Color: green if gap closing, red if opening
```

### Data Fetching with SWR

**hooks/useBattles.ts**

```typescript
export function useBattles(k: number = 5) {
  const { data, error, isLoading } = useSWR(
    `/battles/top?k=${k}`,
    fetcher,
    {
      refreshInterval: 2000,  // Poll every 2s
      dedupingInterval: 1000,
      revalidateOnFocus: false,
    }
  )
  
  // Calculate data age
  const dataAge = data?.updated_at 
    ? Date.now() - new Date(data.updated_at).getTime()
    : null
  
  const connectionStatus = 
    dataAge === null ? "reconnecting" :
    dataAge < 3000 ? "live" :
    dataAge < 10000 ? "delayed" :
    "reconnecting"
  
  return { battles: data?.battles, connectionStatus, isLoading, error }
}
```

### Responsive Design

**Breakpoints**:

- Mobile (< 768px): Single column, stacked battles
- Tablet (768-1024px): Two column grid
- Desktop (> 1024px): Three column with sidebar watchlist

**Mobile Priority** (since users watch on phones):

- Large touch targets (48px min)
- Collapsible sections
- Swipe to pin drivers to watchlist
- Auto-refresh without manual pull

---

## Testing Strategy

### Backend Tests

**Unit Tests** (`tests/test_battle.py`)

```python
def test_battle_score_calculation():
    # Test with various gap/closing scenarios
    
def test_pit_stop_detection():
    # Test sudden gap changes
    
def test_safety_car_penalty():
    # Ensure battles downweighted under yellow
    
def test_battle_stability_filter():
    # New battles should not appear immediately
```

**Integration Tests** (`tests/test_openf1_client.py`)

```python
@pytest.mark.asyncio
async def test_openf1_client_retry_logic():
    # Mock API failures, verify exponential backoff
    
async def test_missing_data_handling():
    # Some drivers missing lap times, etc.
```

**Fixtures** (`tests/fixtures/`)

- `sample_positions.json` - Realistic position data
- `sample_laps.json` - Lap timing data
- `battle_scenarios.json` - Edge cases (pit stops, SC, etc.)

### Frontend Tests

**Component Tests** (using React Testing Library)

```typescript
test("BattleCard shows HOT badge for intense battles", () => {})
test("ConnectionStatus shows delayed when data > 3s old", () => {})
test("Sparkline renders with 6 data points", () => {})
```

**Integration Tests**

```typescript
test("useBattles hook fetches and updates every 2s", async () => {})
test("Graceful degradation when API returns 500", async () => {})
```

---

## Deployment Configuration

### Environment Variables

**Backend `.env`**

```bash
OPENF1_BASE_URL=https://api.openf1.org/v1
POLL_POSITIONS_INTERVAL_S=1.5
POLL_LAPS_INTERVAL_S=10
BATTLE_WATCH_SCORE=0.55
BATTLE_HOT_SCORE=0.70
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:3000,https://yourdomain.com
```

**Frontend `.env.local`**

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### Docker Compose (Local Development)

```yaml
version: '3.8'

services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    env_file:
      - ./backend/.env
    volumes:
      - ./backend/data:/app/data
    
  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
    depends_on:
      - backend
```

### Production Deployment (100% FREE Options)

**IMPORTANT**: All services listed below have free tiers with NO credit card required.

**OpenF1 API** (Data Source)

- **Cost**: Completely FREE, no API key required
- **Rate limits**: None publicly documented (community-maintained API)
- **URL**: [https://api.openf1.org/v1](https://api.openf1.org/v1)
- **Note**: Monitor their status page for any changes

**Backend Options** (Pick ONE - all free):

**Option 1: Render.com (RECOMMENDED)**

- **Free Tier**: 750 hours/month (enough for 24/7 with one app)
- **Specs**: 512MB RAM, shared CPU
- **Limitations**:
  - Spins down after 15 min of inactivity (first request takes ~30s)
  - Good for: Testing and race weekends (manual wake-up before races)
- **Setup**: Connect GitHub repo, auto-deploy on push
- **No credit card needed**: Yes ✓

**Option 2: Railway.app**

- **Free Tier**: $5 credit/month (enough for ~100 hours runtime)
- **Specs**: 512MB RAM, 1 vCPU
- **Limitations**:
  - Not enough for 24/7 (use only during races)
  - ~2-3 full race weekends per month
- **Setup**: GitHub integration
- **No credit card needed**: Yes ✓

**Option 3: Fly.io**

- **Free Tier**: 3 shared-cpu-1x VMs with 256MB RAM
- **Specs**: 256MB RAM per VM
- **Limitations**:
  - Requires credit card for verification (no charges)
  - 160GB outbound data/month
- **Setup**: `fly launch`
- **No credit card needed**: NO (verification only, won't charge)

**Frontend: Vercel (100% FREE)**

- **Free Tier**: Unlimited personal projects
- **Specs**: 100GB bandwidth/month, serverless functions
- **Limitations**: None for this project
- **Setup**: Connect GitHub repo
- **No credit card needed**: Yes ✓

**Alternative Frontend: Netlify**

- **Free Tier**: 100GB bandwidth/month
- **Same as Vercel**: Both are excellent and free
- **No credit card needed**: Yes ✓

**Storage: SQLite (Local File)**

- **Cost**: FREE (just a file on your backend instance)
- **Specs**: Unlimited for our use case
- **Note**: Data persists between deploys on Render

---

### Recommended FREE Stack

For 100% free, zero-cost deployment:

```
┌─────────────────────────────────────┐
│ Frontend: Vercel                    │
│ - Free forever for personal projects│
│ - Auto-deploy from GitHub           │
│ - Global CDN included               │
└─────────────────────────────────────┘
           │
           │ API calls
           ↓
┌─────────────────────────────────────┐
│ Backend: Render.com                 │
│ - Free 750 hrs/month (24/7)         │
│ - Spins down after 15min idle       │
│ - Wake before races (manual)        │
└─────────────────────────────────────┘
           │
           │ HTTP requests
           ↓
┌─────────────────────────────────────┐
│ OpenF1 API (api.openf1.org)         │
│ - Completely FREE                   │
│ - No authentication needed          │
└─────────────────────────────────────┘
```

**Deployment Strategy for Render Free Tier**:

Since Render spins down after 15 min idle, you have two options:

1. **Manual wake-up** (simplest):

- Visit your backend URL before races start
- Takes ~30s to wake up
- Perfect for live race weekends (you'll be watching anyway)

1. **Keep-alive pinger** (automated):

- Use a free service like UptimeRobot or cron-job.org
- Ping your `/health` endpoint every 14 minutes
- Keeps backend awake 24/7 within free tier

**Cost Summary**:

- OpenF1 API: $0/month
- Backend (Render): $0/month
- Frontend (Vercel): $0/month
- Domain (optional): $0 if using Vercel's `.vercel.app` subdomain
- **TOTAL: $0/month**

---

## Build Phases

### Phase 1: Core Backend (Reliable Data Pipeline)

**Goal**: Solid foundation with error handling

**Tasks**:

1. OpenF1 client with retry logic and timeout handling
2. Session manager to track current session and status
3. State manager for in-memory driver states
4. Health endpoint with detailed diagnostics
5. Unit tests for client retry behavior

**Deliverable**: Backend that gracefully handles API failures and reports health status

---

### Phase 2: Battle Detection Engine

**Goal**: Accurate, context-aware battle identification

**Tasks**:

1. Battle scoring algorithm implementation
2. Pit stop detection (sudden gap changes)
3. Safety car awareness (track status integration)
4. Stability filter (minimum duration before showing)
5. Unit tests with edge case fixtures

**Deliverable**: `/battles/top` endpoint returning filtered, scored battles

---

### Phase 3: Frontend Dashboard

**Goal**: Clean, mobile-first live interface

**Tasks**:

1. Connection status banner with data age indicators
2. BattlesList and BattleCard components
3. SWR hooks for auto-refreshing data
4. Responsive layout (mobile-first)
5. Error boundaries for graceful failures

**Deliverable**: Working dashboard showing top 5 battles

---

### Phase 4: Visualizations & Polish

**Goal**: Enhanced user experience

**Tasks**:

1. Sparkline component for gap trends
2. Driver watchlist (pin up to 3 drivers)
3. Session header with lap counter
4. Dark mode support
5. Loading skeletons and stale data indicators

**Deliverable**: Production-ready live companion app

---

### Phase 5: Optional Enhancements

**Tasks**:

1. SQLite persistence for session snapshots
2. Replay mode (browse past sessions)
3. Race Control Timeline tab
4. Predicted overtake windows
5. Mobile PWA support

---

## Success Metrics

**Reliability**:

- Backend uptime > 99% during race sessions
- API errors handled gracefully without crashes
- No false battle alerts during pit stops/SC

**User Experience**:

- Data latency shown clearly (< 3s = green)
- Mobile responsive on 375px+ screens
- Page load time < 2s

**Accuracy**:

- Battles persist for 3+ updates before showing (no flicker)
- Safety car periods properly filtered
- Top 5 battles match real race excitement

---

## Documentation (README.md)

### Sections to Include

1. **Overview** - What this app does (1 paragraph)
2. **Features** - Bullet list of capabilities
3. **Architecture Diagram** - Simple mermaid flowchart
4. **Prerequisites** - Python 3.11+, Node 18+, Docker
5. **Local Setup** - Step-by-step with docker-compose
6. **Configuration** - Environment variables reference
7. **API Documentation** - Endpoints and response schemas
8. **Known Limitations** - OpenF1 API delay (~1-2s), etc.
9. **Future Roadmap** - Phase 5 features
10. **License** - MIT

---

## Free Development Tools (Everything You Need)

All tools and services used in this project are 100% free:

**Development**:

- Python 3.11+ (free, open source)
- Node.js 18+ (free, open source)
- Docker Desktop (free for personal use)
- Git (free, open source)
- VS Code or Cursor (free)

**Libraries**:

- FastAPI (MIT license, free)
- Next.js (MIT license, free)
- React (MIT license, free)
- SQLite (public domain, free)
- All Python packages in `requirements.txt` (free, open source)
- All npm packages in `package.json` (free, open source)

**Services**:

- GitHub (free for public repos)
- Render.com (750 hours/month free tier)
- Vercel (unlimited hobby projects free)
- OpenF1 API (free, no auth required)
- UptimeRobot (50 monitors free) - optional keep-alive

**Total Cost to Build & Deploy**: $0

---

## Cost Verification Checklist

Before starting, verify these remain free:

- **OpenF1 API**: Check [https://openf1.org](https://openf1.org) for any API usage terms updates
- **Render Free Tier**: Verify 750 hours/month still available at render.com/pricing
- **Vercel Free Tier**: Confirm hobby plan at vercel.com/pricing
- **No Hidden Costs**: Ensure no credit card added to any service (except Fly.io for verification if chosen)

**Potential Future Costs to Watch**:

- If OpenF1 introduces rate limits or paid tiers (unlikely, community-maintained)
- If you exceed Vercel's 100GB bandwidth (unlikely for personal use)
- If you want a custom domain ($10-15/year, optional but NOT required)

**Staying Within Free Tiers - Best Practices**:

1. **Bandwidth (Vercel: 100GB/month free)**

- Your app will use ~1-2MB per user per race (2-3 hours)
- Safe estimate: 50+ concurrent users can watch races
- Monitor usage in Vercel dashboard

1. **Backend Runtime (Render: 750 hours/month free)**

- 750 hours = 31.25 days of continuous runtime
- Using keep-alive pinger: 744 hours in a 31-day month (safe)
- Without keep-alive: Only runs during active use (much lower)

1. **API Calls to OpenF1**

- Your backend makes ~40 requests/minute (not frontend)
- ~2,400 requests/hour maximum
- No published rate limits, monitor their GitHub for updates

1. **Storage**

- SQLite file grows ~1MB per race session
- Render free tier includes persistent disk storage
- Manually delete old sessions if needed (keep last 10 races)

**Hard Limit Promise**: This app will NOT generate surprise costs. Free tiers have hard limits that stop service, not "pay if you exceed" models.

---

## Risk Mitigation

| Risk                               | Mitigation                                                                    |
| ---------------------------------- | ----------------------------------------------------------------------------- |
| OpenF1 API goes down mid-race      | Exponential backoff, show "reconnecting" status, keep last known state        |
| OpenF1 API becomes paid/restricted | Monitor their GitHub (openf1/openf1) for announcements                        |
| Pit stops create false battles     | Detect sudden gap changes > 10s, set `pit_window_active` flag                 |
| Safety car bunches field           | Poll session status, downweight battle scores by 50% under yellow             |
| Data delay causes stale UI         | Show data age prominently, use color-coded status (green/yellow/red)          |
| Mobile users have poor connection  | Keep polling payload small (< 10KB), show offline state clearly               |
| Racing starts before app is ready  | Auto-detect active session on startup, backfill last 30s of data if available |
| Render free tier spins down        | Use UptimeRobot free pinger OR manual wake-up before races                    |

---

## Quick Start Commands

### Development

```bash
# Clone and setup
git clone <repo>
cd f1-battle-detector

# Start services
docker-compose up --build

# Backend will be at http://localhost:8000
# Frontend will be at http://localhost:3000
```

### Testing

```bash
# Backend tests
cd backend
pytest tests/ -v

# Frontend tests
cd frontend
npm test
```

### Production (FREE Deployment)

**Deploy Backend to Render (Recommended FREE option)**

```bash
# 1. Push your code to GitHub
git push origin main

# 2. Go to dashboard.render.com
# 3. Click "New +" → "Web Service"
# 4. Connect your GitHub repo
# 5. Settings:
#    - Name: f1-battle-detector-backend
#    - Environment: Docker
#    - Plan: Free
#    - Add environment variables from .env.example
# 6. Click "Create Web Service"
# 7. Copy the service URL (e.g., https://your-app.onrender.com)
```

**Deploy Frontend to Vercel (FREE)**

```bash
# Option 1: CLI
npm i -g vercel
vercel --prod

# Option 2: Dashboard (easier)
# 1. Go to vercel.com/new
# 2. Import your GitHub repo
# 3. Framework: Next.js (auto-detected)
# 4. Add environment variable:
#    NEXT_PUBLIC_API_BASE_URL=https://your-backend.onrender.com
# 5. Deploy
```

**Keep Backend Awake (Optional FREE pinger)**

```bash
# Use UptimeRobot (free):
# 1. Sign up at uptimerobot.com
# 2. Add monitor: HTTP(s)
# 3. URL: https://your-backend.onrender.com/health
# 4. Interval: 14 minutes
# This keeps your Render app awake 24/7 within free tier
```
