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

    first = client.get("/api/battles/top?k=5").json()
    second = client.get("/api/battles/top?k=5").json()

    assert first["count"] == 1, "expected the seeded battle to have matured"
    assert first["battles"] == second["battles"]
    assert first["detected_at"] == second["detected_at"]


def test_many_reads_do_not_mature_a_battle():
    """A battle below the threshold must not surface just because it is read."""
    client, pipeline, clock = fresh_client()
    seed_poll(pipeline, clock, 0)

    for _ in range(50):
        assert client.get("/api/battles/top?k=5").json()["count"] == 0


def test_detection_advances_only_when_the_poll_loop_runs():
    """duration_updates moves on a data poll, and only on a data poll."""
    client, pipeline, clock = fresh_client()
    for poll in range(config.BATTLE_MIN_DURATION_UPDATES):
        seed_poll(pipeline, clock, poll)

    before = client.get("/api/battles/top?k=5").json()["battles"][0]["duration_updates"]
    for _ in range(20):
        client.get("/api/battles/top?k=5")
    unchanged = client.get("/api/battles/top?k=5").json()["battles"][0]["duration_updates"]

    seed_poll(pipeline, clock, config.BATTLE_MIN_DURATION_UPDATES)
    after = client.get("/api/battles/top?k=5").json()["battles"][0]["duration_updates"]

    assert unchanged == before, "20 HTTP reads moved the counter"
    assert after == before + 1, "a real poll did not move the counter"


def test_detected_at_is_the_pipelines_clock_not_the_wall():
    """
    The endpoint reports the instant detection ran in the data's own frame.
    Under replay that is race time, which may be a year in the past.
    """
    client, pipeline, clock = fresh_client()
    seed_poll(pipeline, clock, 0)

    detected_at = client.get("/api/battles/top?k=5").json()["detected_at"]

    assert detected_at == poll_at(0).isoformat()


def test_endpoints_follow_the_active_pipeline():
    """
    Swapping the active pipeline swaps what every endpoint returns, with no
    handler changes. This is what Phase 1 relies on to add replay alongside live.
    """
    client, live, clock = fresh_client()
    for poll in range(config.BATTLE_MIN_DURATION_UPDATES):
        seed_poll(live, clock, poll)

    assert client.get("/api/battles/top?k=5").json()["count"] == 1

    # A second, empty pipeline registered alongside the first
    app.state.pipelines["replay"] = RacePipeline(
        clock=ReplayClock(BASE_TIME), name="replay"
    )
    app.state.active_pipeline = "replay"
    try:
        empty = client.get("/api/battles/top?k=5").json()
        assert empty["count"] == 0
        assert empty["detected_at"] is None
        assert client.get("/api/state/latest").json()["count"] == 0
    finally:
        app.state.active_pipeline = "live"
        app.state.pipelines.pop("replay", None)

    assert client.get("/api/battles/top?k=5").json()["count"] == 1


# --------------------------------------------------------------------------
# The /api prefix (1.5)
# --------------------------------------------------------------------------

def test_every_route_lives_under_api():
    """The container path serves the frontend at /, so nothing else may."""
    paths = {route.path for route in app.routes if hasattr(route, "methods")}

    assert paths, "no routes registered"
    assert all(path.startswith("/api") for path in paths), sorted(paths)


def test_the_old_unprefixed_paths_are_gone():
    client, _, _ = fresh_client()

    assert client.get("/battles/top").status_code == 404
    assert client.get("/health").status_code == 404


def test_the_docs_moved_with_the_api():
    client, _, _ = fresh_client()

    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/openapi.json").json()["info"]["title"] == "F1 Battle Detector API"


def test_replay_status_outside_replay_mode_says_only_the_mode(monkeypatch):
    monkeypatch.setattr(config, "DATA_MODE", "mock")
    client, _, _ = fresh_client()

    status = client.get("/api/replay/status").json()

    assert status["mode"] == "mock"
    assert status["session_key"] is None and status["next_race"] is None


def test_health_reports_the_mode(monkeypatch):
    monkeypatch.setattr(config, "DATA_MODE", "mock")
    client, _, _ = fresh_client()

    health = client.get("/api/health").json()

    assert health["mode"] == "mock"
    assert health["replay"] is None


# --------------------------------------------------------------------------
# The container path: the exported frontend at /, the API still at /api (1.7)
# --------------------------------------------------------------------------

def test_the_frontend_is_served_at_the_root_and_api_still_wins(tmp_path):
    from app.main import mount_frontend

    (tmp_path / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
    # A static file that collides with an API path must not shadow it.
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "health").write_text("static impostor", encoding="utf-8")

    assert mount_frontend(app, str(tmp_path))
    try:
        client, _, _ = fresh_client()
        page = client.get("/")
        assert page.status_code == 200 and "dashboard" in page.text
        assert client.get("/api/health").json()["status"] in ("healthy", "degraded", "unhealthy")
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "name", None) != "frontend"]


def test_no_frontend_directory_means_the_api_alone(tmp_path):
    from app.main import mount_frontend

    assert mount_frontend(app, None) is False
    assert mount_frontend(app, str(tmp_path / "missing")) is False
    assert not [r for r in app.router.routes if getattr(r, "name", None) == "frontend"]
