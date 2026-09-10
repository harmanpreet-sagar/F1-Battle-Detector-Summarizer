"""
FastAPI application entry point with background tasks for polling OpenF1 API.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Dict
import asyncio
import logging

from app.clock import WallClock
from app.config import config
from app.health import get_health_status, health_manager
from app.openf1_client import openf1_client
from app.pipeline import RacePipeline
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

    Phase 1 registers a second one under "replay" and flips
    app.state.active_pipeline; no request handler changes.
    """
    return app.state.pipelines[app.state.active_pipeline]


def reset_for_new_session(session_key):
    """Drop everything accumulated for the previous session."""
    logger.info(f"New session {session_key} - clearing driver state and battles")
    current_pipeline().reset()


async def poll_session_status():
    """Background task to poll for current F1 session."""
    retry_delay = 30.0

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

    logger.info(f"Starting background tasks (DATA_MODE={config.DATA_MODE})...")
    pipeline = current_pipeline()
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
)

# Named pipelines, registered at import time rather than in lifespan so that a
# TestClient built without starting lifespan still has one to read.
_pipelines: Dict[str, RacePipeline] = {
    "live": RacePipeline(clock=WallClock(), name="live"),
}
app.state.pipelines = _pipelines
app.state.active_pipeline = "live"

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "F1 Battle Detector API",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return get_health_status()


@app.get("/session/current")
async def get_current_session():
    """Get current active F1 session."""
    session = session_manager.get_current_session()

    if session:
        return session.model_dump()
    else:
        return {
            "session_key": None,
            "session_name": "No active session",
            "message": "No F1 session is currently active. Check back during a race weekend!"
        }


@app.get("/state/latest")
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


@app.get("/battles/top")
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

        if chaser_info:
            battle_dict["chaser_name"] = chaser_info["full_name"]
            battle_dict["chaser_team"] = chaser_info["team_name"]

        if ahead_info:
            battle_dict["ahead_name"] = ahead_info["full_name"]
            battle_dict["ahead_team"] = ahead_info["team_name"]

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


@app.get("/drivers/{driver_number}/trend")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
