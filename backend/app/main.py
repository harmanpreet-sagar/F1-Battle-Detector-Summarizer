"""
FastAPI application entry point with background tasks for polling OpenF1 API.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging

from app.config import config
from app.health import get_health_status

logger = logging.getLogger(__name__)


# Global task references
position_poll_task = None
lap_poll_task = None
session_poll_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background tasks lifecycle."""
    global position_poll_task, lap_poll_task, session_poll_task
    
    logger.info("Starting background polling tasks...")
    # TODO: Start background tasks here
    # position_poll_task = asyncio.create_task(poll_positions())
    # lap_poll_task = asyncio.create_task(poll_laps())
    # session_poll_task = asyncio.create_task(poll_session_status())
    
    yield
    
    logger.info("Shutting down background tasks...")
    # TODO: Cancel tasks here
    # if position_poll_task:
    #     position_poll_task.cancel()


app = FastAPI(
    title="F1 Battle Detector API",
    description="Real-time F1 battle detection and tracking",
    version="0.1.0",
    lifespan=lifespan,
)

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
    # TODO: Implement
    return {"message": "Not implemented yet"}


@app.get("/state/latest")
async def get_latest_state():
    """Get latest state for all drivers."""
    # TODO: Implement
    return {"message": "Not implemented yet"}


@app.get("/battles/top")
async def get_top_battles(k: int = 5, min_intensity: str = "WATCH"):
    """Get top K battles filtered by minimum intensity."""
    # TODO: Implement
    return {"message": "Not implemented yet"}


@app.get("/drivers/{driver_number}/trend")
async def get_driver_trend(driver_number: int, points: int = 10):
    """Get gap trend for specific driver."""
    # TODO: Implement
    return {"message": "Not implemented yet"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
