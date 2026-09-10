"""
HTTP-level tests for the battle endpoint.

State is seeded directly and no lifespan is started, so the polling tasks never
run - which is the point: these assert what the endpoint does on its own.

The pipeline registered for these tests runs on a ReplayClock, so detection
happens at the instant the seeded data claims to come from. That is also a
standing check that the endpoints read whatever pipeline is registered rather
than a module-level singleton.
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.clock import ReplayClock
from app.config import config
from app.main import app
from app.models import DriverState
from app.pipeline import RacePipeline

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def poll_at(poll: int) -> datetime:
    """The instant poll number `poll` arrives. Seeded data carries the same."""
    return BASE_TIME + timedelta(seconds=4.0 * poll)


def fresh_client():
    """A client plus the pipeline and clock backing it."""
    clock = ReplayClock(BASE_TIME)
    pipeline = RacePipeline(clock=clock, name="test")
    app.state.pipelines["live"] = pipeline
    app.state.active_pipeline = "live"
    return TestClient(app), pipeline, clock


def seed_poll(pipeline, clock, poll: int):
    """Push one poll of a close, closing battle in, then run detection."""
    at = poll_at(poll)
    for number, name, position, gap, lap in (
        (1, "M. Verstappen", 1, None, 90.5),
        (44, "L. Hamilton", 2, 0.35 - 0.01 * poll, 90.0),
    ):
        pipeline.state.update_driver_state(
            DriverState(
                driver_number=number, full_name=name, team_name="Test",
                position=position, gap_to_ahead_s=gap, last_lap_time_s=lap,
                updated_at=at, gap_updated_at=at,
            )
        )
    clock.set(at)
    pipeline.detect()


def test_battles_do_not_advance_on_repeated_reads():
    """
    Duration counting must track data polls, not HTTP requests.

    Detection used to run inside this handler, so two identical reads with no
    new data returned different results - each request advanced
    duration_updates, and two open browser tabs matured battles twice as fast.
    """
    client, pipeline, clock = fresh_client()
    for poll in range(config.BATTLE_MIN_DURATION_UPDATES):
        seed_poll(pipeline, clock, poll)

    first = client.get("/battles/top?k=5").json()
    second = client.get("/battles/top?k=5").json()

    assert first["count"] == 1, "expected the seeded battle to have matured"
    assert first["battles"] == second["battles"]
    assert first["detected_at"] == second["detected_at"]


def test_many_reads_do_not_mature_a_battle():
    """A battle below the threshold must not surface just because it is read."""
    client, pipeline, clock = fresh_client()
    seed_poll(pipeline, clock, 0)

    for _ in range(50):
        assert client.get("/battles/top?k=5").json()["count"] == 0


def test_detection_advances_only_when_the_poll_loop_runs():
    """duration_updates moves on a data poll, and only on a data poll."""
    client, pipeline, clock = fresh_client()
    for poll in range(config.BATTLE_MIN_DURATION_UPDATES):
        seed_poll(pipeline, clock, poll)

    before = client.get("/battles/top?k=5").json()["battles"][0]["duration_updates"]
    for _ in range(20):
        client.get("/battles/top?k=5")
    unchanged = client.get("/battles/top?k=5").json()["battles"][0]["duration_updates"]

    seed_poll(pipeline, clock, config.BATTLE_MIN_DURATION_UPDATES)
    after = client.get("/battles/top?k=5").json()["battles"][0]["duration_updates"]

    assert unchanged == before, "20 HTTP reads moved the counter"
    assert after == before + 1, "a real poll did not move the counter"


def test_detected_at_is_the_pipelines_clock_not_the_wall():
    """
    The endpoint reports the instant detection ran in the data's own frame.
    Under replay that is race time, which may be a year in the past.
    """
    client, pipeline, clock = fresh_client()
    seed_poll(pipeline, clock, 0)

    detected_at = client.get("/battles/top?k=5").json()["detected_at"]

    assert detected_at == poll_at(0).isoformat()


def test_endpoints_follow_the_active_pipeline():
    """
    Swapping the active pipeline swaps what every endpoint returns, with no
    handler changes. This is what Phase 1 relies on to add replay alongside live.
    """
    client, live, clock = fresh_client()
    for poll in range(config.BATTLE_MIN_DURATION_UPDATES):
        seed_poll(live, clock, poll)

    assert client.get("/battles/top?k=5").json()["count"] == 1

    # A second, empty pipeline registered alongside the first
    app.state.pipelines["replay"] = RacePipeline(
        clock=ReplayClock(BASE_TIME), name="replay"
    )
    app.state.active_pipeline = "replay"
    try:
        empty = client.get("/battles/top?k=5").json()
        assert empty["count"] == 0
        assert empty["detected_at"] is None
        assert client.get("/state/latest").json()["count"] == 0
    finally:
        app.state.active_pipeline = "live"
        app.state.pipelines.pop("replay", None)

    assert client.get("/battles/top?k=5").json()["count"] == 1
