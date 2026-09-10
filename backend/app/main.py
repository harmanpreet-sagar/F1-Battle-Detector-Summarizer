"""
FastAPI application entry point with background tasks for polling OpenF1 API.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Dict, Optional
import asyncio
import logging

from app.clock import WallClock
from app.config import config
from app.health import get_health_status, health_manager
from app.openf1_client import openf1_client, OpenF1APIError
from app.pipeline import RacePipeline, TickData
from app.session import session_manager
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


def current_track_status() -> Optional[str]:
    """Track status as the session reports it. Always None live until B4."""
    session = session_manager.get_current_session()
    return session.track_status if session else None


def reset_for_new_session(session_key):
    """Drop everything accumulated for the previous session."""
    logger.info(f"New session {session_key} - clearing driver state and battles")
    current_pipeline().reset()


async def poll_session_status():
    """Background task to poll for current F1 session."""
    retry_delay = 30.0

    while True:
        try:
            # TEST MODE: Use mock session
            if config.TEST_MODE:
                logger.info("TEST MODE: Using mock session")
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


async def poll_positions():
    """Background task to poll position data and update driver states."""
    retry_delay = 1.0
    max_retry_delay = 30.0

    # Lap data changes once a lap, so it is refreshed on its own slower cadence
    # and reused across position polls.
    cached_laps: dict = {}
    laps_fetched_at = None

    while True:
        try:
            pipeline = current_pipeline()

            # TEST MODE: Use mock data
            if config.TEST_MODE:
                logger.info("TEST MODE: Generating mock driver data")
                pipeline.ingest(TickData(
                    states=mock_data_generator.generate_driver_states(),
                    track_status=current_track_status(),
                ))
                pipeline.detect()
                health_manager.record_successful_position_poll()
                health_manager.active_session = True

                await asyncio.sleep(config.POLL_POSITIONS_INTERVAL_S)
                continue

            # NORMAL MODE: Real OpenF1 data
            # Only poll if we have an active session
            if not session_manager.is_session_active():
                await asyncio.sleep(5)
                continue

            session = session_manager.get_current_session()
            session_key = session.session_key

            # Get latest positions and driver info. Gaps come from /intervals -
            # /position carries position numbers only.
            positions = await openf1_client.get_latest_positions(session_key)
            intervals = await openf1_client.get_latest_intervals(session_key)
            drivers_data = await openf1_client.get_drivers(session_key)

            # Refresh lap data on its own cadence
            loop_now = asyncio.get_event_loop().time()
            if laps_fetched_at is None or (loop_now - laps_fetched_at) >= config.POLL_LAPS_INTERVAL_S:
                cached_laps = await openf1_client.get_latest_laps(
                    session_key, count=config.BATTLE_PACE_TREND_WINDOW
                )
                laps_fetched_at = loop_now
                health_manager.record_successful_lap_poll()
                logger.debug(f"Refreshed lap data for {len(cached_laps)} drivers")

            # Create driver info lookup
            drivers_info = {d["driver_number"]: d for d in drivers_data if "driver_number" in d}

            # Update state manager
            if positions:
                pipeline.ingest(TickData(
                    positions=positions,
                    intervals=intervals,
                    drivers_info=drivers_info,
                    laps=cached_laps,
                    track_status=current_track_status(),
                ))
                pipeline.detect()
                health_manager.record_successful_position_poll()
                logger.debug(
                    f"Updated positions for {len(positions)} drivers "
                    f"({len(intervals)} interval rows)"
                )

            # Reset retry delay on success
            retry_delay = config.POLL_POSITIONS_INTERVAL_S
            await asyncio.sleep(config.POLL_POSITIONS_INTERVAL_S)

        except OpenF1APIError as e:
            logger.error(f"OpenF1 API error in position polling: {e}")
            health_manager.record_error()
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)

        except Exception as e:
            logger.exception(f"Unexpected error in position polling: {e}")
            health_manager.record_error()
            await asyncio.sleep(retry_delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background tasks lifecycle."""
    global position_poll_task, session_poll_task

    logger.info("Starting background polling tasks...")
    session_poll_task = asyncio.create_task(poll_session_status())
    position_poll_task = asyncio.create_task(poll_positions())

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
