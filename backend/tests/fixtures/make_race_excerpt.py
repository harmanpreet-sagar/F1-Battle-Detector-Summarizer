"""
Regenerate tests/fixtures/race_excerpt/ from the cached 2025 Hungarian GP.

    cd backend
    python -m tests.fixtures.make_race_excerpt

The excerpt is the opening laps of a real race, trimmed so that what the
live-equivalence test serves through a fake OpenF1 is small enough to poll
several hundred times in a few seconds. It keeps everything a live poll during
those laps could have seen: each driver's last pre-race position and gap (the
grid the race starts from), every row up to the cutoff, and the stints and
pit stops inside it. It contains the lap-5 pit stop of car 27 and the opening
laps' position changes. There is no safety car in this race.

`session_result` is left out on purpose. It names the finishers of the full
race, and a classified DNF later in the afternoon would retire that car at the
end of a stream that stops on lap 8.
"""
from datetime import timedelta
from pathlib import Path
from typing import Dict, List

from app.replay.store import SessionStore
from app.replay.timeline import build_timeline
from app.state import parse_openf1_timestamp

SOURCE_SESSION = 9928
LAPS = 8
OUT = Path(__file__).resolve().parent / "race_excerpt"


def _when(row: Dict, field: str = "date"):
    return parse_openf1_timestamp(row.get(field))


def _latest_before(rows: List[Dict], moment) -> List[Dict]:
    latest: Dict[int, Dict] = {}
    for row in rows:
        at = _when(row)
        if at is None or at >= moment:
            continue
        current = latest.get(row["driver_number"])
        if current is None or at > _when(current):
            latest[row["driver_number"]] = row
    return list(latest.values())


def build() -> Path:
    archive = SessionStore().load(SOURCE_SESSION)
    timeline = build_timeline(archive)
    start = timeline.start

    leader_laps = [
        event for event in timeline.events
        if event.kind == "lap" and event.lap_number == LAPS
    ]
    cutoff = min(event.at for event in leader_laps) + timedelta(seconds=1)

    def in_race(rows, field="date"):
        return [r for r in rows if _when(r, field) and start <= _when(r, field) <= cutoff]

    rows = {
        "drivers": archive.rows("drivers"),
        "position": _latest_before(archive.rows("position"), start) + in_race(archive.rows("position")),
        "intervals": _latest_before(archive.rows("intervals"), start) + in_race(archive.rows("intervals")),
        "laps": [r for r in archive.rows("laps")
                 if _when(r, "date_start") and _when(r, "date_start") <= cutoff],
        "race_control": [r for r in archive.rows("race_control")
                         if _when(r) and _when(r) <= cutoff],
        "stints": [r for r in archive.rows("stints") if (r.get("lap_start") or 99) <= LAPS],
        "pit": in_race(archive.rows("pit")),
        "session_result": [],
        "overtakes": [],
        "starting_grid": [],
    }

    manifest = archive.manifest
    manifest.endpoints = {}
    manifest.date_end = cutoff
    store = SessionStore(OUT)
    store.save(manifest, rows)
    return store.path_for(SOURCE_SESSION)


if __name__ == "__main__":
    path = build()
    total = sum(f.stat().st_size for f in path.iterdir())
    print(f"wrote {path} ({total / 1024:.0f} KB)")
