"""
HTTP-level tests for the battle endpoint.

State is seeded directly and no lifespan is started, so the polling tasks never
run - which is the point: these assert what the endpoint does on its own.
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.battle import battle_detector
from app.config import config
from app.main import app, run_detection
from app.models import DriverState
from app.state import state_manager

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def poll_at(poll: int) -> datetime:
    """The instant poll number `poll` arrives. Seeded data carries the same."""
    return BASE_TIME + timedelta(seconds=4.0 * poll)


def seed_battle(poll: int):
    """Push one poll of a close, closing battle into the state manager."""
    at = poll_at(poll)
    for number, name, position, gap, lap in (
        (1, "M. Verstappen", 1, None, 90.5),
        (44, "L. Hamilton", 2, 0.35 - 0.01 * poll, 90.0),
    ):
        state_manager.update_driver_state(
            DriverState(
                driver_number=number, full_name=name, team_name="Test",
                position=position, gap_to_ahead_s=gap, last_lap_time_s=lap,
                updated_at=at, gap_updated_at=at,
            )
        )


def fresh_client():
    state_manager.clear()
    battle_detector.reset()
    return TestClient(app)


def test_battles_do_not_advance_on_repeated_reads():
    """
    Duration counting must track data polls, not HTTP requests.

    Detection used to run inside this handler, so two identical reads with no
    new data returned different results - each request advanced
    duration_updates, and two open browser tabs matured battles twice as fast.
    """
    client = fresh_client()
    for poll in range(config.BATTLE_MIN_DURATION_UPDATES):
        seed_battle(poll)
        run_detection(poll_at(poll))

    first = client.get("/battles/top?k=5").json()
    second = client.get("/battles/top?k=5").json()

    assert first["count"] == 1, "expected the seeded battle to have matured"
    assert first["battles"] == second["battles"]
    assert first["detected_at"] == second["detected_at"]


def test_many_reads_do_not_mature_a_battle():
    """A battle below the threshold must not surface just because it is read."""
    client = fresh_client()
    seed_battle(0)
    run_detection(poll_at(0))

    for _ in range(50):
        assert client.get("/battles/top?k=5").json()["count"] == 0


def test_detection_advances_only_when_the_poll_loop_runs():
    """duration_updates moves on a data poll, and only on a data poll."""
    client = fresh_client()
    for poll in range(config.BATTLE_MIN_DURATION_UPDATES):
        seed_battle(poll)
        run_detection(poll_at(poll))

    before = client.get("/battles/top?k=5").json()["battles"][0]["duration_updates"]
    for _ in range(20):
        client.get("/battles/top?k=5")
    unchanged = client.get("/battles/top?k=5").json()["battles"][0]["duration_updates"]

    seed_battle(config.BATTLE_MIN_DURATION_UPDATES)
    run_detection(poll_at(config.BATTLE_MIN_DURATION_UPDATES))
    after = client.get("/battles/top?k=5").json()["battles"][0]["duration_updates"]

    assert unchanged == before, "20 HTTP reads moved the counter"
    assert after == before + 1, "a real poll did not move the counter"
