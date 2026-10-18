"""Data sources feeding the pipeline: live, mock and replay."""
from app.sources.base import DataSource
from app.sources.live import LiveSource
from app.sources.mock import MockSource
from app.sources.replay import ReplaySource

__all__ = ["DataSource", "LiveSource", "MockSource", "ReplaySource", "build_source"]


def build_source(mode: str, clock, sessions) -> DataSource:
    """Construct the source for a DATA_MODE value."""
    if mode == "mock":
        return MockSource(clock, sessions)
    if mode == "live":
        return LiveSource(clock, sessions)
    if mode == "replay":
        return ReplaySource(clock)
    raise ValueError(f"unknown DATA_MODE {mode!r}")
