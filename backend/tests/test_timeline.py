"""
Tests for the timeline builder.

The synthetic race in `race_rows.py` stands in for a real one. Each edge case
the plan lists - null lap durations, null start dates, "+1 LAP" gaps, pre-race
rows, red-flag silence, retirements - gets its own test, because every one of
them is a way for a replay to look plausible while being wrong.
"""
from datetime import timedelta

import pytest

from app.replay.timeline import build_timeline, track_status_from
from tests import race_rows
from tests.race_rows import DRIVERS, LAP_S, LAPS, RACE_START, SESSION_START, at


def timeline_of(tmp_path, rows=None, **manifest_overrides):
    return build_timeline(race_rows.make_archive(tmp_path, rows, **manifest_overrides))


def kinds(timeline, kind):
    return [event for event in timeline.events if event.kind == kind]


# --------------------------------------------------------------------------
# Ordering and bounds
# --------------------------------------------------------------------------

def test_events_come_out_in_time_order(tmp_path):
    timeline = timeline_of(tmp_path)

    stamps = [event.at for event in timeline.events]
    assert stamps == sorted(stamps)
    assert timeline.session_key == race_rows.SESSION_KEY
    assert timeline.drivers == set(DRIVERS)


def test_building_the_same_race_twice_gives_the_same_stream(tmp_path):
    """Ties are broken deterministically, so two builds compare equal."""
    first = timeline_of(tmp_path)
    second = timeline_of(tmp_path)

    assert first.events == second.events


def test_replay_starts_at_lights_out_not_at_the_feed(tmp_path):
    """
    The timing feed opens before the formation lap. Starting there means
    minutes of a stationary grid before anything happens.
    """
    timeline = timeline_of(tmp_path)

    assert timeline.start == RACE_START
    assert timeline.start > SESSION_START
    assert all(event.at >= timeline.start for event in timeline.events)


def test_pre_race_rows_are_dropped(tmp_path):
    """The grid position rows are stamped before lights out."""
    rows = race_rows.endpoints()
    timeline = timeline_of(tmp_path, rows)

    grid_rows = [r for r in rows["position"] if r["date"] < RACE_START.isoformat()]
    assert grid_rows  # the fixture really does contain some
    assert not [e for e in kinds(timeline, "position") if e.at < RACE_START]


def test_the_grid_is_carried_into_the_start(tmp_path):
    """
    /position emits only on change, so a car holding its grid slot has no
    position event for a long time after lights out. A live poll at the start
    would still return its grid row, so the timeline keeps each driver's last
    pre-race position and gap as the state the race starts from.
    """
    rows = race_rows.endpoints()
    rows["position"].append({
        "session_key": race_rows.SESSION_KEY, "driver_number": 16, "position": 3,
        "date": (RACE_START - timedelta(seconds=10)).isoformat(),
    })
    timeline = timeline_of(tmp_path, rows)

    carried = {(e.kind, e.driver_number): e for e in timeline.carried}
    assert {d for kind, d in carried if kind == "position"} == set(DRIVERS)
    # The latest pre-race row per driver, not the first
    assert carried[("position", 16)].at == RACE_START - timedelta(seconds=10)
    assert all(e.at < RACE_START for e in timeline.carried)


def test_a_session_start_after_lights_out_wins(tmp_path):
    """
    Whichever candidate is later wins. A trimmed archive whose session starts
    mid-race must not replay from a lap-1 timestamp it has no data for.
    """
    later = RACE_START + timedelta(seconds=120)

    timeline = timeline_of(tmp_path, date_start=later)

    assert timeline.start == later


def test_the_end_is_the_last_event(tmp_path):
    timeline = timeline_of(tmp_path)

    assert timeline.end == max(event.at for event in timeline.events)
    assert timeline.duration.total_seconds() == pytest.approx(LAPS * LAP_S, abs=5)


def test_between_and_until_slice_the_stream(tmp_path):
    timeline = timeline_of(tmp_path)
    moment = at(100)

    window = timeline.between(at(90), moment)

    assert all(at(90) < event.at <= moment for event in window)
    assert window == [e for e in timeline.until(moment) if e.at > at(90)]


# --------------------------------------------------------------------------
# Laps (fixes B6)
# --------------------------------------------------------------------------

def test_a_lap_is_known_when_it_finishes_not_when_it_starts(tmp_path):
    timeline = timeline_of(tmp_path)

    first_lap = min(kinds(timeline, "lap"), key=lambda e: e.at)

    assert first_lap.lap_number == 1
    assert first_lap.at == RACE_START + timedelta(seconds=LAP_S)
    assert first_lap.lap_duration_s == LAP_S


def test_the_raw_lap_row_rides_along(tmp_path):
    """The pipeline is fed OpenF1 rows, not our parsed shapes. See 1.3."""
    timeline = timeline_of(tmp_path)

    lap = kinds(timeline, "lap")[0]

    assert lap.row["st_speed"] == 320
    assert lap.row["driver_number"] == lap.driver_number


def test_a_lap_with_no_duration_never_completes(tmp_path):
    """An in-progress lap, a pit lap or a lap ended by a red flag."""
    laps = race_rows.all_lap_rows()
    laps[1] = dict(laps[1], lap_duration=None)

    timeline = timeline_of(tmp_path, race_rows.endpoints(laps=laps))

    completed = {(e.driver_number, e.lap_number) for e in kinds(timeline, "lap")}
    assert (laps[1]["driver_number"], laps[1]["lap_number"]) not in completed


def test_a_lap_with_no_start_date_falls_back_to_the_previous_lap(tmp_path):
    """The previous lap ended at the same instant this one began."""
    laps = race_rows.all_lap_rows()
    second = next(i for i, r in enumerate(laps)
                  if r["driver_number"] == 1 and r["lap_number"] == 2)
    laps[second] = dict(laps[second], date_start=None)

    timeline = timeline_of(tmp_path, race_rows.endpoints(laps=laps))

    lap_two = next(e for e in kinds(timeline, "lap")
                   if e.driver_number == 1 and e.lap_number == 2)
    assert lap_two.at == RACE_START + timedelta(seconds=2 * LAP_S)


def test_a_first_lap_with_no_start_date_is_skipped(tmp_path):
    """Nothing to anchor to: there is no previous lap to end where it began."""
    laps = race_rows.all_lap_rows()
    first = next(i for i, r in enumerate(laps)
                 if r["driver_number"] == 1 and r["lap_number"] == 1)
    laps[first] = dict(laps[first], date_start=None)

    timeline = timeline_of(tmp_path, race_rows.endpoints(laps=laps))

    driver_one = [e for e in kinds(timeline, "lap") if e.driver_number == 1]
    assert 1 not in {e.lap_number for e in driver_one}
    assert 2 in {e.lap_number for e in driver_one}


def test_an_unfinished_lap_does_not_anchor_the_next_one(tmp_path):
    """
    Lap 2 never completed, so lap 3 has no previous end to borrow. Its own
    date_start is all it can use - and inheriting a stale anchor instead would
    put lap 3 in the wrong place entirely.
    """
    laps = race_rows.all_lap_rows()
    for index, row in enumerate(laps):
        if row["driver_number"] != 1:
            continue
        if row["lap_number"] == 2:
            laps[index] = dict(row, lap_duration=None)
        if row["lap_number"] == 3:
            laps[index] = dict(row, date_start=None)

    timeline = timeline_of(tmp_path, race_rows.endpoints(laps=laps))

    driver_one = {e.lap_number for e in kinds(timeline, "lap") if e.driver_number == 1}
    assert driver_one == {1, 4}


# --------------------------------------------------------------------------
# Gaps
# --------------------------------------------------------------------------

def test_a_lapped_cars_gap_string_becomes_no_gap(tmp_path):
    """
    "+1 LAP" is not a gap in seconds. Reading it as one would put a lapped
    car in a battle with the car it is a lap behind.
    """
    intervals = race_rows.interval_rows()
    intervals[2] = dict(intervals[2], interval="+1 LAP", gap_to_leader="+1 LAP")

    timeline = timeline_of(tmp_path, race_rows.endpoints(intervals=intervals))

    lapped = next(e for e in kinds(timeline, "interval")
                  if e.row["interval"] == "+1 LAP")
    assert lapped.interval_s is None
    assert lapped.gap_to_leader_s is None


def test_the_leaders_null_interval_stays_null(tmp_path):
    timeline = timeline_of(tmp_path)

    leader = next(e for e in kinds(timeline, "interval") if e.driver_number == DRIVERS[0])

    assert leader.interval_s is None
    assert leader.gap_to_leader_s == 0


def test_a_gap_row_with_no_timestamp_is_dropped(tmp_path):
    intervals = race_rows.interval_rows()
    intervals[0] = dict(intervals[0], date=None)

    timeline = timeline_of(tmp_path, race_rows.endpoints(intervals=intervals))

    assert len(kinds(timeline, "interval")) == len(intervals) - 1


# --------------------------------------------------------------------------
# Track status (fixes B4)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("row,expected", [
    ({"category": "Flag", "flag": "GREEN", "scope": "Track"}, "green"),
    ({"category": "Flag", "flag": "CLEAR", "scope": "Track"}, "green"),
    ({"category": "Flag", "flag": "CHEQUERED", "scope": "Track"}, "green"),
    ({"category": "Flag", "flag": "RED", "scope": "Track"}, "red"),
    ({"category": "Flag", "flag": "YELLOW", "scope": "Sector"}, "yellow"),
    ({"category": "Flag", "flag": "DOUBLE YELLOW", "scope": "Sector"}, "yellow"),
    ({"category": "SafetyCar", "message": "SAFETY CAR DEPLOYED"}, "sc"),
    ({"category": "SafetyCar", "message": "VIRTUAL SAFETY CAR DEPLOYED"}, "vsc"),
    ({"category": "Flag", "flag": "BLACK AND WHITE", "scope": "Driver"}, None),
    ({"category": "Flag", "flag": "YELLOW", "scope": "Driver"}, None),
    ({"category": "Drs", "message": "DRS ENABLED"}, None),
    ({"category": "SafetyCar", "message": "SAFETY CAR IN THIS LAP"}, None),
    ({"category": "SafetyCar", "message": "VIRTUAL SAFETY CAR ENDING"}, None),
    # Steward notices mention the safety car without deploying one
    ({"category": "Other", "message": "TURN 4 INCIDENT INVOLVING CARS 30 (LAW) AND 18 (STR) "
                                      "NOTED - SAFETY CAR INFRINGEMENT"}, None),
    ({"category": "Other", "message": "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 1 (VER) - "
                                      "OVERTAKING UNDER SAFETY CAR"}, None),
    # Rows without a category still deploy on the usual wording
    ({"message": "SAFETY CAR DEPLOYED"}, "sc"),
    ({"message": "VIRTUAL SAFETY CAR DEPLOYED"}, "vsc"),
])
def test_race_control_messages_map_to_a_track_status(row, expected):
    assert track_status_from(row) == expected


def test_the_stream_opens_with_an_explicit_status(tmp_path):
    """
    The pipeline needs a track status from its first tick, not from the first
    time one changes.
    """
    timeline = timeline_of(tmp_path)

    first = timeline.events[0]

    assert first.kind == "track_status"
    assert first.at == timeline.start
    assert first.status == "green"
    assert timeline.initial_track_status == "green"


def test_a_safety_car_and_its_restart_are_both_recorded(tmp_path):
    """
    The sector yellow precedes the deploy, as it does in a real feed: the
    marshals wave before race control sends the car out.
    """
    timeline = timeline_of(tmp_path)

    statuses = [(e.at, e.status) for e in kinds(timeline, "track_status")]

    assert statuses == [
        (timeline.start, "green"),
        (at(95), "yellow"),
        (at(100), "sc"),
        (at(260), "green"),
    ]


def test_a_sector_yellow_cannot_end_a_safety_car(tmp_path):
    """
    An SC period is full of sector yellows. Letting one downgrade the status
    would restart battle detection mid-neutralisation, which is B4 in reverse.
    """
    timeline = timeline_of(tmp_path)

    during_sc = [e.status for e in kinds(timeline, "track_status")
                 if at(100) <= e.at < at(260)]

    assert during_sc == ["sc"]


def test_a_race_starting_under_a_safety_car_knows_it(tmp_path):
    """
    Everything race control said before lights out sets the status the replay
    opens under, rather than producing events of its own.
    """
    control = [
        dict(race_rows.race_control_rows()[0]),
        {"date": race_rows.iso(at(-20)), "category": "SafetyCar",
         "message": "SAFETY CAR DEPLOYED"},
    ]

    timeline = timeline_of(tmp_path, race_rows.endpoints(race_control=control))

    assert timeline.initial_track_status == "sc"
    assert timeline.events[0].status == "sc"


def test_a_red_flag_holds_until_the_track_is_clear(tmp_path):
    control = race_rows.race_control_rows()[:1] + [
        {"date": race_rows.iso(at(100)), "category": "Flag", "flag": "RED",
         "scope": "Track", "message": "RED FLAG"},
        {"date": race_rows.iso(at(150)), "category": "Flag", "flag": "YELLOW",
         "scope": "Sector", "message": "YELLOW IN TRACK SECTOR 2"},
        {"date": race_rows.iso(at(300)), "category": "Flag", "flag": "CLEAR",
         "scope": "Track", "message": "TRACK CLEAR"},
    ]

    timeline = timeline_of(tmp_path, race_rows.endpoints(race_control=control))

    assert [e.status for e in kinds(timeline, "track_status")] == ["green", "red", "green"]


def test_repeated_messages_do_not_repeat_the_status(tmp_path):
    """
    Race control repeats itself constantly. Only changes are events, or the
    stream carries hundreds of "still green" entries.
    """
    plain = race_rows.race_control_rows()
    repeated = plain + [
        {"date": race_rows.iso(at(280)), "category": "Flag", "flag": "GREEN",
         "scope": "Track", "message": "GREEN LIGHT"},
        {"date": race_rows.iso(at(290)), "category": "Flag", "flag": "CLEAR",
         "scope": "Track", "message": "TRACK CLEAR"},
    ]

    before = timeline_of(tmp_path / "a", race_rows.endpoints(race_control=plain))
    after = timeline_of(tmp_path / "b", race_rows.endpoints(race_control=repeated))

    assert len(kinds(after, "track_status")) == len(kinds(before, "track_status"))


# --------------------------------------------------------------------------
# Stints and pit stops (fixes B5)
# --------------------------------------------------------------------------

def test_a_stint_starts_when_its_first_lap_begins(tmp_path):
    timeline = timeline_of(tmp_path)

    new_tyres = next(e for e in kinds(timeline, "stint")
                     if e.driver_number == 44 and e.lap_start == 3)

    assert new_tyres.compound == "HARD"
    # Lap 3 begins where lap 2 ended.
    assert new_tyres.at == RACE_START + timedelta(seconds=2 * LAP_S)


def test_an_opening_stint_is_known_from_its_first_completed_lap(tmp_path):
    """There is no lap 0 to end, so lap 1 completing is the earliest anchor."""
    timeline = timeline_of(tmp_path)

    opening = next(e for e in kinds(timeline, "stint")
                   if e.driver_number == 1 and e.lap_start == 1)

    assert opening.compound == "MEDIUM"
    assert opening.at == RACE_START + timedelta(seconds=LAP_S)


def test_a_stint_whose_laps_never_completed_is_dropped(tmp_path):
    stints = race_rows.stint_rows() + [
        {"driver_number": 1, "compound": "SOFT", "lap_start": 40, "lap_end": 50},
    ]

    timeline = timeline_of(tmp_path, race_rows.endpoints(stints=stints))

    assert 40 not in {e.lap_start for e in kinds(timeline, "stint")}


def test_a_pit_stop_lands_at_its_own_timestamp(tmp_path):
    timeline = timeline_of(tmp_path)

    stop, = kinds(timeline, "pit")

    assert stop.driver_number == 44
    assert stop.lap_number == 2
    assert stop.pit_duration_s == 22.5


# --------------------------------------------------------------------------
# Red flags and other silence
# --------------------------------------------------------------------------

def test_a_normal_race_has_no_quiet_periods(tmp_path):
    timeline = timeline_of(tmp_path)

    assert timeline.quiet_periods(threshold_s=60) == []


def test_a_stoppage_shows_up_as_a_quiet_period(tmp_path):
    """
    A red flag empties the feed. Replaying that at race speed is a frozen
    screen, so 1.3 compresses it - which needs it found first.
    """
    stoppage_starts, stoppage_ends = 120.0, 900.0
    rows = race_rows.endpoints(
        position=[r for r in race_rows.position_rows()
                  if not _inside(r["date"], stoppage_starts, stoppage_ends)],
        intervals=[r for r in race_rows.interval_rows()
                   if not _inside(r["date"], stoppage_starts, stoppage_ends)],
        laps=race_rows.all_lap_rows(laps=1),
        stints=[], pit=[],
    )

    timeline = timeline_of(tmp_path, rows)
    periods = timeline.quiet_periods(threshold_s=60)

    assert len(periods) == 1
    start, end = periods[0]
    assert (end - start).total_seconds() > 60


def _inside(stamp: str, first_s: float, last_s: float) -> bool:
    return at(first_s).isoformat() <= stamp <= at(last_s).isoformat()


# --------------------------------------------------------------------------
# Retirements
# --------------------------------------------------------------------------

def test_a_driver_who_finishes_is_never_retired(tmp_path):
    timeline = timeline_of(tmp_path)

    assert kinds(timeline, "retirement") == []


def test_a_car_that_stops_reporting_is_retired(tmp_path):
    """
    Without this, the last position and gap a retired car ever reported sit in
    the running order for the rest of the race, inventing battles against a
    car that is in the garage.
    """
    stops_at = LAP_S  # end of lap 1, with three laps still to run
    rows = race_rows.endpoints(
        position=race_rows.position_rows(until_s={16: stops_at}),
        intervals=race_rows.interval_rows(until_s={16: stops_at}),
        session_result=[],
    )

    timeline = timeline_of(tmp_path, rows)

    retirement, = kinds(timeline, "retirement")
    assert retirement.driver_number == 16
    assert retirement.reason == "no data"
    # Two laps of grace, so the car is not dropped over one missed poll.
    assert retirement.at >= at(stops_at) + timedelta(seconds=2 * LAP_S)


def test_a_classified_dnf_is_retired_even_at_the_very_end(tmp_path):
    """
    A car that stops on the last lap has no two laps of silence to detect.
    session_result names who did not finish; the rows say when.
    """
    rows = race_rows.endpoints(session_result=race_rows.session_result_rows(dnf=(44,)))

    timeline = timeline_of(tmp_path, rows)

    retirement, = kinds(timeline, "retirement")
    assert retirement.driver_number == 44
    assert retirement.reason == "classified DNF"
    assert retirement.at <= timeline.end


def test_a_short_gap_in_the_feed_is_not_a_retirement(tmp_path):
    """One dropped poll is not a car in the garage."""
    intervals = [r for r in race_rows.interval_rows()
                 if not _inside(r["date"], 60, 75) or r["driver_number"] != 16]
    rows = race_rows.endpoints(intervals=intervals, session_result=[])

    timeline = timeline_of(tmp_path, rows)

    assert kinds(timeline, "retirement") == []


# --------------------------------------------------------------------------
# Archives that are missing pieces
# --------------------------------------------------------------------------

def test_a_race_with_no_completed_laps_starts_at_the_session(tmp_path):
    """
    There is no lap 1 to work back from, so `session.date_start` is the only
    anchor left. A trimmed excerpt can look like this.
    """
    timeline = timeline_of(tmp_path, race_rows.endpoints(laps=[], stints=[]))

    assert timeline.start == SESSION_START


def test_with_neither_laps_nor_a_start_date_the_first_row_wins(tmp_path):
    timeline = timeline_of(
        tmp_path, race_rows.endpoints(laps=[], stints=[]), date_start=None,
    )

    assert timeline.start == min(event.at for event in timeline.events)


def test_rows_with_no_driver_number_are_dropped(tmp_path):
    rows = race_rows.endpoints(
        position=race_rows.position_rows() + [{"position": 1, "date": race_rows.iso(at(10))}],
        stints=race_rows.stint_rows() + [{"compound": "SOFT", "lap_start": 1}],
        pit=race_rows.pit_rows() + [{"date": race_rows.iso(at(20))}],
        laps=race_rows.all_lap_rows() + [{"lap_number": 1, "lap_duration": 90.0}],
    )

    timeline = timeline_of(tmp_path, rows)

    assert all(getattr(e, "driver_number", 1) in DRIVERS for e in timeline.events)


def test_a_pit_row_with_no_timestamp_is_dropped(tmp_path):
    pit = [dict(race_rows.pit_rows()[0], date=None)]

    timeline = timeline_of(tmp_path, race_rows.endpoints(pit=pit))

    assert kinds(timeline, "pit") == []


def test_an_archive_with_nothing_in_it_is_an_error(tmp_path):
    """Better here than as an empty replay that silently shows no battles."""
    empty = {name: [] for name in race_rows.endpoints()}

    with pytest.raises(ValueError, match="no events to replay"):
        timeline_of(tmp_path, empty, date_start=None, date_end=None)


def test_a_sector_clear_cannot_end_a_safety_car(tmp_path):
    """
    Race control clears the incident while the safety car is still
    circulating: "CLEAR IN TRACK SECTOR 3" arrives mid-SC. Reading that as a
    green restarts battle detection with the field bunched up behind the
    safety car, which is exactly the wave of fake battles B4 is about.
    """
    control = [
        {"date": race_rows.iso(at(-30)), "category": "Flag", "flag": "GREEN",
         "scope": "Track", "message": "GREEN LIGHT"},
        {"date": race_rows.iso(at(100)), "category": "SafetyCar",
         "message": "SAFETY CAR DEPLOYED"},
        {"date": race_rows.iso(at(140)), "category": "Flag", "flag": "CLEAR",
         "scope": "Sector", "sector": 3, "message": "CLEAR IN TRACK SECTOR 3"},
        {"date": race_rows.iso(at(260)), "category": "Flag", "flag": "CLEAR",
         "scope": "Track", "message": "TRACK CLEAR"},
    ]

    timeline = timeline_of(tmp_path, race_rows.endpoints(race_control=control))

    assert [(e.at, e.status) for e in kinds(timeline, "track_status")] == [
        (timeline.start, "green"),
        (at(100), "sc"),
        (at(260), "green"),
    ]


def test_a_sector_clear_does_end_a_plain_sector_yellow(tmp_path):
    """With no neutralisation running, a cleared sector is a green track."""
    control = [
        {"date": race_rows.iso(at(-30)), "category": "Flag", "flag": "GREEN",
         "scope": "Track", "message": "GREEN LIGHT"},
        {"date": race_rows.iso(at(100)), "category": "Flag", "flag": "YELLOW",
         "scope": "Sector", "sector": 3, "message": "YELLOW IN TRACK SECTOR 3"},
        {"date": race_rows.iso(at(140)), "category": "Flag", "flag": "CLEAR",
         "scope": "Sector", "sector": 3, "message": "CLEAR IN TRACK SECTOR 3"},
    ]

    timeline = timeline_of(tmp_path, race_rows.endpoints(race_control=control))

    assert [e.status for e in kinds(timeline, "track_status")] == [
        "green", "yellow", "green",
    ]
