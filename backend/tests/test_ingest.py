"""
Tests for `python -m app.ingest`.

Driven by a stub client throughout: the point of the 45-minute guard and of
the idempotency check is that we fetch a race once and never again, and a test
suite that hit OpenF1 to prove it would be doing the opposite.
"""
from datetime import datetime, timedelta, timezone
import asyncio

import pytest

from app.config import config
from app.ingest import IngestError, RaceIngestor, build_parser, format_report
from app.openf1_client import OpenF1APIError
from app.replay.store import ENDPOINTS, SessionStore
from tests import race_rows
from tests.race_rows import SESSION_END, SESSION_KEY

# Long enough after the session that the free archive has settled.
WELL_AFTER = SESSION_END + timedelta(hours=3)


class StubClient:
    """Serves the synthetic race, and remembers what was asked for."""

    def __init__(self, rows=None, sessions=None, fails=(), meetings=None):
        self._rows = rows if rows is not None else race_rows.endpoints()
        self._sessions = sessions if sessions is not None else [race_rows.session_row()]
        self._meetings = meetings if meetings is not None else [MEETING]
        self._fails = set(fails)
        self.requested = []

    async def get_endpoint(self, endpoint, params=None):
        self.requested.append(endpoint)
        if endpoint in self._fails:
            raise OpenF1APIError(f"HTTP 404 for /{endpoint}")
        if endpoint == "sessions":
            return self._sessions
        if endpoint == "meetings":
            return self._meetings
        return self._rows.get(endpoint, [])

    @property
    def data_requests(self):
        """Endpoint fetches, excluding the session and meeting lookups."""
        return [name for name in self.requested if name not in ("sessions", "meetings")]


MEETING = {
    "meeting_key": 1250,
    "meeting_name": "Italian Grand Prix",
    "meeting_official_name": "FORMULA 1 PIRELLI GRAN PREMIO D'ITALIA 2025",
}


def a_session_without_its_meeting():
    """What OpenF1's /sessions really returns: no meeting name at all."""
    row = race_rows.session_row()
    row.pop("meeting_name")
    row.pop("meeting_official_name")
    return row


def ingestor(tmp_path, client=None, now=WELL_AFTER, **kwargs):
    return RaceIngestor(
        client=client or StubClient(),
        store=SessionStore(tmp_path),
        now=lambda: now,
        pause_s=0,  # the real pause is politeness, not correctness
        **kwargs,
    )


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Resolving which race to fetch
# --------------------------------------------------------------------------

def test_session_key_resolves_directly(tmp_path):
    client = StubClient()

    session = run(ingestor(tmp_path, client).resolve_session(session_key=SESSION_KEY))

    assert session["session_key"] == SESSION_KEY
    assert client.data_requests == []


def test_an_unknown_session_key_is_an_error(tmp_path):
    client = StubClient(sessions=[])

    with pytest.raises(IngestError, match="knows no session"):
        run(ingestor(tmp_path, client).resolve_session(session_key=1234))


def test_round_is_the_nth_race_of_the_season_by_date(tmp_path):
    """
    OpenF1 carries no round number, so ingest derives one. Rows come back in
    no guaranteed order, which is exactly why they are sorted first.
    """
    client = StubClient(sessions=[
        race_rows.session_row(session_key=9003, date_start="2025-05-18T13:00:00+00:00"),
        race_rows.session_row(session_key=9001, date_start="2025-03-16T13:00:00+00:00"),
        race_rows.session_row(session_key=9002, date_start="2025-04-06T13:00:00+00:00"),
    ])

    resolve = ingestor(tmp_path, client).resolve_session

    assert run(resolve(year=2025, round_number=1))["session_key"] == 9001
    assert run(resolve(year=2025, round_number=3))["session_key"] == 9003


def test_a_round_past_the_end_of_the_season_says_how_many_there_are(tmp_path):
    client = StubClient(sessions=[race_rows.session_row()])

    with pytest.raises(IngestError, match="has 1 rounds"):
        run(ingestor(tmp_path, client).resolve_session(year=2025, round_number=14))


def test_a_year_with_no_races_is_an_error(tmp_path):
    client = StubClient(sessions=[])

    with pytest.raises(IngestError, match="no Race sessions found for 2099"):
        run(ingestor(tmp_path, client).resolve_session(year=2099, round_number=1))


def test_naming_neither_a_key_nor_a_round_is_an_error(tmp_path):
    with pytest.raises(IngestError, match="--session-key"):
        run(ingestor(tmp_path).resolve_session(year=2025))


# --------------------------------------------------------------------------
# The free-tier guard
# --------------------------------------------------------------------------

def test_a_session_still_inside_its_live_window_is_refused(tmp_path):
    """
    OpenF1's free archive settles after the live window closes. Fetching
    early caches a partial race that looks complete.
    """
    just_ended = SESSION_END + timedelta(minutes=10)
    client = StubClient()

    with pytest.raises(IngestError, match="try again in 35 minutes"):
        run(ingestor(tmp_path, client, now=just_ended).run(session_key=SESSION_KEY))

    assert client.data_requests == []


def test_the_guard_opens_exactly_45_minutes_after_the_session_ends(tmp_path):
    client = StubClient()
    on_the_boundary = SESSION_END + timedelta(minutes=45)

    result = run(ingestor(tmp_path, client, now=on_the_boundary).run(
        session_key=SESSION_KEY
    ))

    assert result.fetched is True


def test_a_session_with_no_end_date_is_fetched_anyway(tmp_path):
    """Nothing to check against. Log it and carry on rather than refuse."""
    client = StubClient(sessions=[race_rows.session_row(date_end=None)])

    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert result.fetched is True


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def test_a_full_run_caches_every_endpoint(tmp_path):
    client = StubClient()

    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert result.fetched is True
    assert client.data_requests == list(ENDPOINTS)
    assert set(result.manifest.endpoints) == set(ENDPOINTS)
    assert result.manifest.circuit == "Monza"
    assert result.manifest.fetched_at == WELL_AFTER


def test_the_round_number_is_recorded_when_it_was_given(tmp_path):
    result = run(ingestor(tmp_path).run(year=2025, round_number=1))

    assert result.manifest.round == 1


def test_a_failing_optional_endpoint_costs_only_that_endpoint(tmp_path):
    """`session_result` and `starting_grid` are absent for older races."""
    client = StubClient(fails=("session_result", "starting_grid"))

    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert result.fetched is True
    assert result.manifest.endpoints["session_result"].missing is True
    assert result.manifest.endpoints["laps"].rows > 0


def test_a_failing_required_endpoint_fails_the_run(tmp_path):
    client = StubClient(fails=("intervals",))

    with pytest.raises(IngestError, match="/intervals failed"):
        run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))


def test_an_empty_required_endpoint_fails_the_run(tmp_path):
    """
    A race with no interval rows has no gaps, so it has no battles. Caching it
    would mean discovering that at replay time instead.
    """
    client = StubClient(rows=race_rows.endpoints(intervals=[]))

    with pytest.raises(IngestError, match="not replayable"):
        run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert not SessionStore(tmp_path).has(SESSION_KEY)


# --------------------------------------------------------------------------
# Fetching each race once
# --------------------------------------------------------------------------

def test_a_second_run_fetches_nothing(tmp_path):
    run(ingestor(tmp_path).run(session_key=SESSION_KEY))

    client = StubClient()
    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert result.fetched is False
    assert client.data_requests == []


def test_force_refetches_but_still_writes_nothing_when_nothing_changed(tmp_path):
    first = run(ingestor(tmp_path).run(session_key=SESSION_KEY))

    client = StubClient()
    again = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY, force=True))

    assert client.data_requests == list(ENDPOINTS)
    assert again.fetched is False
    assert again.manifest.fetched_at == first.manifest.fetched_at


def test_force_rewrites_when_the_data_did_change(tmp_path):
    run(ingestor(tmp_path).run(session_key=SESSION_KEY))

    client = StubClient(rows=race_rows.endpoints(overtakes=[{"driver_number": 44}]))
    again = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY, force=True))

    assert again.fetched is True
    assert again.manifest.endpoints["overtakes"].rows == 1


def test_an_incomplete_cache_is_completed(tmp_path):
    """
    A run interrupted part way leaves a manifest missing endpoints. That is
    not "already cached", and the next run must finish the job.
    """
    store = SessionStore(tmp_path)
    store.save(race_rows.manifest(), {"drivers": race_rows.driver_rows()})

    client = StubClient()
    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert result.fetched is True
    assert client.data_requests == list(ENDPOINTS)


def test_a_dry_run_resolves_the_session_and_stops(tmp_path):
    client = StubClient()

    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY, dry_run=True))

    assert result.dry_run is True
    assert client.data_requests == []
    assert not SessionStore(tmp_path).has(SESSION_KEY)


# --------------------------------------------------------------------------
# The measurement 1.1 asks for
# --------------------------------------------------------------------------

def test_the_report_gives_rows_and_size_per_endpoint(tmp_path):
    result = run(ingestor(tmp_path).run(session_key=SESSION_KEY))

    report = format_report(result.manifest)

    assert "session_key 9999" in report
    for endpoint in ENDPOINTS:
        assert endpoint in report
    assert "KB" in report and "MB" in report
    assert f"{result.manifest.total_rows:,}" in report


def test_the_report_marks_an_absent_endpoint(tmp_path):
    client = StubClient(rows=race_rows.endpoints(starting_grid=[]))
    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert "absent" in format_report(result.manifest)


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------

def test_the_cli_accepts_both_ways_of_naming_a_race():
    parser = build_parser()

    by_round = parser.parse_args(["--year", "2025", "--round", "14"])
    by_key = parser.parse_args(["--session-key", "9693"])

    assert (by_round.year, by_round.round_number) == (2025, 14)
    assert by_key.session_key == 9693
    assert by_key.session_name == "Race"


# --------------------------------------------------------------------------
# The command itself
# --------------------------------------------------------------------------

class ClosableStubClient(StubClient):
    """`main()` closes the client on the way out, network or not."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False

    async def close(self):
        self.closed = True


def cli(monkeypatch, argv, client=None):
    """Run main() with the module-level client replaced."""
    from app import ingest

    stub = client or ClosableStubClient()
    monkeypatch.setattr(ingest, "openf1_client", stub)
    return run(ingest.main(argv)), stub


def test_the_command_caches_a_race_and_reports_it(monkeypatch, capsys, tmp_path):
    """
    The real wiring: argv in, cached race and a size report out. The fixture
    race ran in 2025, so the live-window guard passes on real wall time; only
    the politeness pause is turned off, to keep the test quick.
    """
    monkeypatch.setattr(config, "INGEST_REQUEST_PAUSE_S", 0)

    code, client = cli(monkeypatch, ["--session-key", str(SESSION_KEY),
                                     "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert client.closed is True
    assert "Monza" in out and "total" in out
    assert str(tmp_path) in out
    assert SessionStore(tmp_path).has(SESSION_KEY)


def test_the_command_lists_what_is_cached(monkeypatch, capsys, tmp_path):
    run(ingestor(tmp_path).run(session_key=SESSION_KEY))

    code, client = cli(monkeypatch, ["--list", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "1 cached race(s)" in out
    assert "9999" in out and "Monza" in out
    assert client.requested == []


def test_listing_an_empty_cache_says_so(monkeypatch, capsys, tmp_path):
    code, _ = cli(monkeypatch, ["--list", "--data-dir", str(tmp_path)])

    assert code == 0
    assert "No cached races" in capsys.readouterr().out


def test_a_refusal_exits_non_zero_with_the_reason(monkeypatch, capsys, tmp_path):
    code, _ = cli(monkeypatch, ["--data-dir", str(tmp_path)])
    err = capsys.readouterr().err

    assert code == 1
    assert "--session-key" in err


# --------------------------------------------------------------------------
# The meeting name
# --------------------------------------------------------------------------

def test_the_meeting_name_comes_from_meetings_when_the_session_lacks_it(tmp_path):
    client = StubClient(sessions=[a_session_without_its_meeting()])

    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert result.manifest.meeting_name == "Italian Grand Prix"
    assert SessionStore(tmp_path).read_manifest(SESSION_KEY).meeting_name == "Italian Grand Prix"
    assert client.requested.count("meetings") == 1


def test_the_short_name_beats_the_official_one(tmp_path):
    """The official title is the sponsor's; a header wants "Italian Grand Prix"."""
    result = run(ingestor(tmp_path).run(session_key=SESSION_KEY))

    assert result.manifest.meeting_name == "Italian Grand Prix"


def test_no_meeting_lookup_when_the_session_already_names_it(tmp_path):
    client = StubClient()

    run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert "meetings" not in client.requested


def test_a_failed_meeting_lookup_costs_only_the_name(tmp_path):
    client = StubClient(sessions=[a_session_without_its_meeting()], fails=("meetings",))

    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert result.fetched is True
    assert result.manifest.meeting_name is None


def test_a_cached_race_without_a_name_is_backfilled_without_refetching(tmp_path):
    """Races cached before ingest looked names up get one; their data is untouched."""
    store = SessionStore(tmp_path)
    nameless = StubClient(sessions=[a_session_without_its_meeting()], fails=("meetings",))
    run(ingestor(tmp_path, nameless).run(session_key=SESSION_KEY))
    before = {p.name: p.read_bytes() for p in store.path_for(SESSION_KEY).glob("*.json.gz")}

    client = StubClient(sessions=[a_session_without_its_meeting()])
    result = run(ingestor(tmp_path, client).run(session_key=SESSION_KEY))

    assert result.fetched is False
    assert client.data_requests == []
    assert store.read_manifest(SESSION_KEY).meeting_name == "Italian Grand Prix"
    after = {p.name: p.read_bytes() for p in store.path_for(SESSION_KEY).glob("*.json.gz")}
    assert after == before
