"""
A tiny synthetic race, shaped exactly like OpenF1's rows.

Three drivers, four laps, one safety car. It is not a real race and is not
meant to be: it exists so the store and the timeline can be tested without a
network and without committing megabytes. The ~10-lap excerpt of a real race
belongs to 1.8, where it backs the live-equivalence test.

Every helper takes overrides, so a test that cares about one broken field
builds the whole race and then breaks that field.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.replay.store import Manifest, SessionArchive, SessionStore

SESSION_KEY = 9999
# The timing feed opens before the formation lap; the race starts later.
SESSION_START = datetime(2025, 9, 7, 13, 0, 0, tzinfo=timezone.utc)
RACE_START = datetime(2025, 9, 7, 13, 5, 0, tzinfo=timezone.utc)
SESSION_END = datetime(2025, 9, 7, 13, 20, 0, tzinfo=timezone.utc)
LAP_S = 90.0
LAPS = 4
DRIVERS = (1, 44, 16)


def iso(moment: datetime) -> str:
    """OpenF1 stamps carry microseconds and an explicit offset."""
    return moment.isoformat()


def at(seconds: float) -> datetime:
    """A moment `seconds` after lights out."""
    return RACE_START + timedelta(seconds=seconds)


def session_row(**overrides) -> Dict:
    row = {
        "session_key": SESSION_KEY,
        "meeting_key": 1250,
        "year": 2025,
        "session_name": "Race",
        "session_type": "Race",
        "circuit_short_name": "Monza",
        "meeting_name": "Italian Grand Prix",
        "meeting_official_name": "FORMULA 1 GRAN PREMIO D'ITALIA 2025",
        "date_start": iso(SESSION_START),
        "date_end": iso(SESSION_END),
        "gmt_offset": "02:00:00",
    }
    row.update(overrides)
    return row


def driver_rows() -> List[Dict]:
    names = {
        1: ("Max VERSTAPPEN", "VER", "Red Bull Racing"),
        44: ("Lewis HAMILTON", "HAM", "Ferrari"),
        16: ("Charles LECLERC", "LEC", "Ferrari"),
    }
    return [
        {
            "session_key": SESSION_KEY,
            "driver_number": number,
            "full_name": full,
            "name_acronym": acronym,
            "team_name": team,
            "team_colour": "3671C6",
        }
        for number, (full, acronym, team) in names.items()
    ]


def lap_rows(driver_number: int, laps: int = LAPS, duration: float = LAP_S) -> List[Dict]:
    """`laps` consecutive completed laps, each `duration` long."""
    return [
        {
            "session_key": SESSION_KEY,
            "driver_number": driver_number,
            "lap_number": lap,
            "date_start": iso(at((lap - 1) * duration)),
            "lap_duration": duration,
            "is_pit_out_lap": False,
            "st_speed": 320,
        }
        for lap in range(1, laps + 1)
    ]


def all_lap_rows(laps: int = LAPS) -> List[Dict]:
    return [row for driver in DRIVERS for row in lap_rows(driver, laps=laps)]


def position_rows(cadence_s: float = 30.0, laps: int = LAPS,
                  until_s: Optional[Dict[int, float]] = None) -> List[Dict]:
    """
    A position row per driver every `cadence_s`, plus the pre-race grid.

    `until_s` stops one driver's rows early, which is how a retirement looks
    in the archive: the car simply stops reporting.
    """
    until_s = until_s or {}
    rows = [
        {
            "session_key": SESSION_KEY,
            "driver_number": driver,
            "position": index + 1,
            "date": iso(SESSION_START + timedelta(seconds=60)),
        }
        for index, driver in enumerate(DRIVERS)
    ]

    total = laps * LAP_S
    elapsed = 0.0
    while elapsed <= total:
        for index, driver in enumerate(DRIVERS):
            if elapsed > until_s.get(driver, total):
                continue
            rows.append({
                "session_key": SESSION_KEY,
                "driver_number": driver,
                "position": index + 1,
                "date": iso(at(elapsed)),
            })
        elapsed += cadence_s
    return rows


def interval_rows(cadence_s: float = 4.0, laps: int = LAPS,
                  until_s: Optional[Dict[int, float]] = None) -> List[Dict]:
    """Gap rows on OpenF1's ~4s cadence. The leader's own interval is null."""
    until_s = until_s or {}
    rows = []
    total = laps * LAP_S
    elapsed = 0.0
    while elapsed <= total:
        for index, driver in enumerate(DRIVERS):
            if elapsed > until_s.get(driver, total):
                continue
            rows.append({
                "session_key": SESSION_KEY,
                "driver_number": driver,
                "interval": None if index == 0 else round(0.8 + 0.1 * index, 2),
                "gap_to_leader": 0 if index == 0 else round(0.8 * index, 2),
                "date": iso(at(elapsed)),
            })
        elapsed += cadence_s
    return rows


def race_control_rows() -> List[Dict]:
    """Green at the start, a safety car over lap 2, green again on lap 3."""
    return [
        {"session_key": SESSION_KEY, "date": iso(at(-30)), "category": "Flag",
         "flag": "GREEN", "scope": "Track", "message": "GREEN LIGHT - PIT EXIT OPEN"},
        {"session_key": SESSION_KEY, "date": iso(at(95)), "category": "Flag",
         "flag": "YELLOW", "scope": "Sector", "sector": 4,
         "message": "YELLOW IN TRACK SECTOR 4"},
        {"session_key": SESSION_KEY, "date": iso(at(100)), "category": "SafetyCar",
         "flag": None, "scope": "Track", "message": "SAFETY CAR DEPLOYED"},
        {"session_key": SESSION_KEY, "date": iso(at(150)), "category": "Flag",
         "flag": "DOUBLE YELLOW", "scope": "Sector", "sector": 4,
         "message": "DOUBLE YELLOW IN TRACK SECTOR 4"},
        {"session_key": SESSION_KEY, "date": iso(at(230)), "category": "SafetyCar",
         "flag": None, "scope": "Track", "message": "SAFETY CAR IN THIS LAP"},
        {"session_key": SESSION_KEY, "date": iso(at(260)), "category": "Flag",
         "flag": "CLEAR", "scope": "Track", "message": "TRACK CLEAR"},
    ]


def stint_rows() -> List[Dict]:
    """Every driver starts on mediums; 44 switches to hards on lap 3."""
    rows = [
        {"session_key": SESSION_KEY, "driver_number": driver, "stint_number": 1,
         "compound": "MEDIUM", "lap_start": 1, "lap_end": LAPS,
         "tyre_age_at_start": 0}
        for driver in DRIVERS
    ]
    rows[1] = dict(rows[1], lap_end=2)
    rows.append({
        "session_key": SESSION_KEY, "driver_number": 44, "stint_number": 2,
        "compound": "HARD", "lap_start": 3, "lap_end": LAPS, "tyre_age_at_start": 0,
    })
    return rows


def pit_rows() -> List[Dict]:
    return [{
        "session_key": SESSION_KEY, "driver_number": 44, "lap_number": 2,
        "date": iso(at(2 * LAP_S - 5)), "pit_duration": 22.5, "lane_duration": 22.5,
    }]


def session_result_rows(dnf: tuple = ()) -> List[Dict]:
    return [
        {"session_key": SESSION_KEY, "driver_number": driver, "position": index + 1,
         "dnf": driver in dnf, "dns": False, "dsq": False, "number_of_laps": LAPS}
        for index, driver in enumerate(DRIVERS)
    ]


def endpoints(**overrides) -> Dict[str, List[Dict]]:
    """Every endpoint of the synthetic race, ready to hand to the store."""
    rows = {
        "session_result": session_result_rows(),
        "drivers": driver_rows(),
        "position": position_rows(),
        "intervals": interval_rows(),
        "laps": all_lap_rows(),
        "race_control": race_control_rows(),
        "stints": stint_rows(),
        "pit": pit_rows(),
        "overtakes": [],
        "starting_grid": [],
    }
    rows.update(overrides)
    return rows


def manifest(**overrides) -> Manifest:
    values = {
        "session_key": SESSION_KEY,
        "year": 2025,
        "round": 16,
        "circuit": "Monza",
        "meeting_name": "Italian Grand Prix",
        "session_name": "Race",
        "date_start": SESSION_START,
        "date_end": SESSION_END,
        "fetched_at": SESSION_END + timedelta(hours=2),
    }
    values.update(overrides)
    return Manifest(**values)


def make_archive(tmp_path: Path, rows: Optional[Dict[str, List[Dict]]] = None,
                 **manifest_overrides) -> SessionArchive:
    """Write the synthetic race to a temp store and open it for reading."""
    store = SessionStore(tmp_path)
    store.save(manifest(**manifest_overrides), rows if rows is not None else endpoints())
    return store.load(SESSION_KEY)
