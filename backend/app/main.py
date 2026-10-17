"""
FastAPI application entry point with background tasks for polling OpenF1 API.
"""
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Dict
import asyncio
import logging

from app import demo
from app.clock import ReplayClock, WallClock, utcnow
from app.config import config
from app.health import get_health_status, health_manager
from app.openf1_client import openf1_client
from app.pipeline import RacePipeline
from app.replay.broadcast import get_broadcast
from app.replay.store import SCHEMA_VERSION, SessionStore
from app.session import session_manager
from app.sources import build_source
from app.mock_data import mock_data_generator

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Global task references
position_poll_task = None
session_poll_task = None


def current_pipeline() -> RacePipeline:
    """
    The pipeline endpoints read from.

    Under DEMO_STATELESS there is no background loop to have filled one, so the
    race - the cached replay, or the mock one - is rebuilt up to the current
    instant and thrown away after the response. Every handler below is
    unchanged either way - that is the whole reason detection was moved behind
    a pipeline object.
    """
    if config.DEMO_STATELESS:
        if config.DATA_MODE == "replay":
            return get_broadcast().build_pipeline(utcnow())
        return demo.build_pipeline(utcnow())
    return app.state.pipelines[app.state.active_pipeline]


def reset_for_new_session(session_key):
    """Drop everything accumulated for the previous session."""
    logger.info(f"New session {session_key} - clearing driver state and battles")
    current_pipeline().reset()


async def poll_session_status():
    """Background task to poll for current F1 session."""
    retry_delay = 30.0

    if config.DATA_MODE == "replay":
        # The broadcast schedule is the session: /session/current reads it
        # straight from the clock, and ReplaySource marks the first tick of
        # each race as a reset. Nothing here needs to poll, least of all
        # OpenF1.
        return

    while True:
        try:
            # MOCK MODE: Use a generated session
            if config.DATA_MODE == "mock":
                logger.debug("Mock mode: using generated session")
                if session_manager.set_session(mock_data_generator.generate_mock_session()):
                    reset_for_new_session(session_manager.current_session.session_key)
                health_manager.active_session = True
                await asyncio.sleep(config.POLL_SESSION_INTERVAL_S)
                continue

            # NORMAL MODE: Real OpenF1 session
            if await session_manager.update_current_session():
                reset_for_new_session(session_manager.current_session.session_key)

            if session_manager.is_session_active():
                health_manager.active_session = True
                logger.debug(f"Active session: {session_manager.current_session.session_name}")
            else:
                health_manager.active_session = False
                logger.debug("No active session")

            await asyncio.sleep(config.POLL_SESSION_INTERVAL_S)

        except Exception as e:
            logger.error(f"Session polling error: {e}", exc_info=True)
            health_manager.record_error()
            await asyncio.sleep(retry_delay)


async def run_source(source, pipeline: RacePipeline):
    """
    The one loop that drives everything.

    Every mode is this same three lines; what differs is only which source is
    plugged in and what its clock says. Phase 1 adds ReplaySource and this does
    not change.
    """
    logger.info(f"Running {source.name} source into '{pipeline.name}' pipeline")
    async for tick in source.ticks():
        pipeline.ingest(tick)
        pipeline.detect()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background tasks lifecycle."""
    global position_poll_task, session_poll_task

    if config.DEMO_STATELESS:
        # Nothing to start: a serverless host freezes the process between
        # requests, so a poll loop here would advance a pipeline no handler
        # reads and bill for the privilege.
        logger.info(
            f"Stateless demo mode (DATA_MODE={config.DATA_MODE}); "
            "no background tasks"
        )
        yield
        await openf1_client.close()
        return

    logger.info(f"Starting background tasks (DATA_MODE={config.DATA_MODE})...")
    pipeline = app.state.pipelines[app.state.active_pipeline]
    source = build_source(config.DATA_MODE, pipeline.clock, session_manager)

    session_poll_task = asyncio.create_task(poll_session_status())
    position_poll_task = asyncio.create_task(run_source(source, pipeline))

    yield

    logger.info("Shutting down background tasks...")
    if session_poll_task:
        session_poll_task.cancel()
        try:
            await session_poll_task
        except asyncio.CancelledError:
            pass

    if position_poll_task:
        position_poll_task.cancel()
        try:
            await position_poll_task
        except asyncio.CancelledError:
            pass

    # Close HTTP client
    await openf1_client.close()


app = FastAPI(
    title="F1 Battle Detector API",
    description="Real-time F1 battle detection and tracking",
    version="0.1.0",
    lifespan=lifespan,
    # Everything the backend serves lives under /api, docs included, so that
    # the container path can serve the frontend at / from the same origin.
    docs_url="/api/docs",
    swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)
api = APIRouter(prefix="/api")

# Named pipelines, registered at import time rather than in lifespan so that a
# TestClient built without starting lifespan still has one to read.
_pipelines: Dict[str, RacePipeline] = {
    "live": RacePipeline(clock=WallClock(), name="live"),
    # Its clock is moved to race time by ReplaySource before every tick.
    "replay": RacePipeline(clock=ReplayClock(utcnow()), name="replay"),
}
app.state.pipelines = _pipelines
app.state.active_pipeline = "replay" if config.DATA_MODE == "replay" else "live"

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    # Preview deployments get a new hostname on every push; the exact-origin
    # list above cannot keep up with them.
    allow_origin_regex=config.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@api.get("")
async def root():
    """Root endpoint."""
    return {
        "message": "F1 Battle Detector API",
        "version": "0.1.0",
        "mode": config.DATA_MODE,
        "docs": "/api/docs",
    }


@api.get("/health")
async def health():
    """Health check endpoint, plus which mode is serving and where replay is."""
    if config.DEMO_STATELESS:
        # There is no poll loop to have recorded a successful poll, and the
        # default reading of that absence - "cannot connect to OpenF1" - is
        # wrong here. Data is generated on demand, so it is current by
        # construction.
        health_manager.record_successful_position_poll()
        health_manager.active_session = True
    status = get_health_status()
    status["mode"] = config.DATA_MODE
    status["replay"] = None
    if config.DATA_MODE == "replay":
        broadcast = get_broadcast()
        position = broadcast.locate(utcnow())
        manifest = broadcast.replay(position.index).archive.manifest
        status["replay"] = {
            "session_key": position.session_key,
            "race_time": position.race_time.isoformat(),
            "progress_pct": round(position.progress * 100, 1),
            "races": len(broadcast.slots),
            # The cache format this build reads, and the one the race on air
            # was written with. A mismatch is refused at load time.
            "schema_version": SCHEMA_VERSION,
            "manifest_schema_version": manifest.schema_version,
        }
    return status


@api.get("/replay/status")
async def get_replay_status():
    """
    Where the broadcast is: race, race time, lap, track status, speed.

    Outside replay mode every field but `mode` is null, so the frontend can
    call it unconditionally and decide what header to draw from `mode`.
    """
    if config.DATA_MODE != "replay":
        return {
            "mode": config.DATA_MODE,
            **{field: None for field in (
                "session_key", "meeting_name", "circuit", "year", "race_time",
                "elapsed_s", "lap", "total_laps", "track_status", "stoppage", "speed",
                "progress_pct", "next_race",
            )},
        }
    return {"mode": "replay", **get_broadcast().status_at(utcnow())}


@api.get("/replay/sessions")
async def get_replay_sessions():
    """Every cached race, from its manifest, oldest first."""
    playlist = set()
    if config.DATA_MODE == "replay":
        playlist = {slot.replay.session_key for slot in get_broadcast().slots}
    return {
        "sessions": [
            {
                "session_key": manifest.session_key,
                "year": manifest.year,
                "round": manifest.round,
                "circuit": manifest.circuit,
                "meeting_name": manifest.meeting_name,
                "session_name": manifest.session_name,
                "date_start": manifest.date_start.isoformat() if manifest.date_start else None,
                "rows": manifest.total_rows,
                "bytes": manifest.total_bytes,
                "schema_version": manifest.schema_version,
                "in_playlist": manifest.session_key in playlist,
            }
            for manifest in SessionStore().manifests()
        ],
    }


@api.get("/session/current")
async def get_current_session():
    """Get current active F1 session."""
    if config.DATA_MODE == "replay":
        # A pure function of the wall clock on both paths, so it agrees with
        # whatever the pipeline is showing.
        return get_broadcast().session_at(utcnow()).model_dump()

    if config.DEMO_STATELESS:
        # No session poll loop either; derive the lap from the clock.
        return demo.build_session(utcnow()).model_dump()

    session = session_manager.get_current_session()

    if session:
        return session.model_dump()
    else:
        return {
            "session_key": None,
            "session_name": "No active session",
            "message": "No F1 session is currently active. Check back during a race weekend!"
        }


@api.get("/state/latest")
async def get_latest_state():
    """Get latest state for all drivers."""
    states = current_pipeline().current_states()

    if not states:
        return {
            "drivers": [],
            "count": 0,
            "message": "No driver data available. Waiting for active session."
        }

    # Sort by position
    states_sorted = sorted(states, key=lambda s: s.position)

    return {
        "drivers": [s.model_dump() for s in states_sorted],
        "count": len(states),
        "updated_at": states_sorted[0].updated_at.isoformat() if states_sorted else None
    }


@api.get("/battles/top")
async def get_top_battles(k: int = 5, min_intensity: str = "WATCH"):
    """
    Get top K battles filtered by minimum intensity.

    A pure read of the last detection run. Detection happens in the poll loop,
    so the result does not depend on who is asking or how often.
    """
    pipeline = current_pipeline()
    driver_states = pipeline.current_states()

    if pipeline.detected_at is None:
        return {
            "battles": [],
            "count": 0,
            "message": "No driver data available. Waiting for active session with position data.",
            "updated_at": None,
            "detected_at": None
        }

    all_battles = pipeline.latest_battles()

    # Filter by minimum intensity
    intensity_order = {"HOT": 2, "WATCH": 1, "NONE": 0}
    min_intensity_value = intensity_order.get(min_intensity.upper(), 1)

    filtered_battles = [
        b for b in all_battles
        if intensity_order.get(b.intensity, 0) >= min_intensity_value
    ]

    # Take top K
    top_battles = filtered_battles[:k]

    # Enrich battles with driver names for frontend convenience
    enriched_battles = []
    for battle in top_battles:
        battle_dict = battle.model_dump()

        # Add driver names
        chaser_info = pipeline.driver_info(battle.chaser_driver_number)
        ahead_info = pipeline.driver_info(battle.ahead_driver_number)

        for side, info in (("chaser", chaser_info), ("ahead", ahead_info)):
            if not info:
                continue
            battle_dict[f"{side}_name"] = info["full_name"]
            battle_dict[f"{side}_team"] = info["team_name"]
            battle_dict[f"{side}_acronym"] = info["name_acronym"]
            battle_dict[f"{side}_team_colour"] = info["team_colour"]
            battle_dict[f"{side}_tyre"] = {
                "compound": info["tire_compound"],
                "age_laps": info["tire_age_laps"],
                "pit_stops": info["pit_stops_count"],
            }

        enriched_battles.append(battle_dict)

    return {
        "battles": enriched_battles,
        "count": len(top_battles),
        "total_detected": len(all_battles),
        # Freshness of the underlying F1 data, which is what the client shows as
        # its connection status; detected_at is when this result was computed.
        "updated_at": driver_states[0].updated_at.isoformat() if driver_states else None,
        "detected_at": pipeline.detected_at.isoformat()
    }


@api.get("/drivers/{driver_number}/trend")
async def get_driver_trend(driver_number: int, points: int = 10):
    """Get gap trend for specific driver."""
    history = current_pipeline().history(driver_number)

    if not history:
        return {
            "driver_number": driver_number,
            "trend": [],
            "message": f"No history available for driver {driver_number}"
        }

    # Take last N points
    recent_history = history[-points:] if len(history) > points else history

    trend_data = [
        {
            "timestamp": state.updated_at.isoformat(),
            "gap_to_ahead_s": state.gap_to_ahead_s,
            "gap_to_leader_s": state.gap_to_leader_s,
            "position": state.position
        }
        for state in recent_history
    ]

    return {
        "driver_number": driver_number,
        "trend": trend_data,
        "count": len(trend_data)
    }


app.include_router(api)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
