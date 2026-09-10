"""Data sources feeding the pipeline: live, mock, and (Phase 1) replay."""
from app.sources.base import DataSource
from app.sources.live import LiveSource
from app.sources.mock import MockSource

__all__ = ["DataSource", "LiveSource", "MockSource", "build_source"]


def build_source(mode: str, clock, sessions) -> DataSource:
    """Construct the source for a DATA_MODE value."""
    if mode == "mock":
        return MockSource(clock, sessions)
    if mode == "live":
        return LiveSource(clock, sessions)
    if mode == "replay":
        raise NotImplementedError(
            "DATA_MODE=replay arrives in Phase 1; use mock or live for now"
        )
    raise ValueError(f"unknown DATA_MODE {mode!r}")
