"""
Tests for the on-disk race cache.

Everything here runs against a temp directory holding the synthetic race from
`race_rows.py`. Nothing reaches the network.
"""
import gzip
import json

import pytest

from app.replay.store import (
    ENDPOINTS,
    SCHEMA_VERSION,
    EndpointRecord,
    Manifest,
    SessionStore,
    content_hash,
    write_rows,
)
from tests import race_rows
from tests.race_rows import SESSION_KEY


def test_save_then_load_returns_the_same_rows(tmp_path):
    rows = race_rows.endpoints()
    archive = race_rows.make_archive(tmp_path, rows)

    assert archive.rows("laps") == rows["laps"]
    assert archive.rows("intervals") == rows["intervals"]
    assert archive.session_key == SESSION_KEY


def test_every_endpoint_is_written_compressed(tmp_path):
    race_rows.make_archive(tmp_path)

    directory = SessionStore(tmp_path).path_for(SESSION_KEY)
    written = {path.name for path in directory.glob("*.json.gz")}

    assert written == {f"{name}.json.gz" for name in ENDPOINTS}


def test_manifest_records_rows_size_and_hash(tmp_path):
    rows = race_rows.endpoints()
    archive = race_rows.make_archive(tmp_path, rows)

    record = archive.manifest.endpoints["intervals"]
    assert record.rows == len(rows["intervals"])
    assert record.bytes > 0
    assert record.hash == content_hash(rows["intervals"])
    assert archive.manifest.total_rows == sum(len(r) for r in rows.values())


def test_manifest_survives_a_round_trip_through_json(tmp_path):
    original = race_rows.make_archive(tmp_path).manifest

    restored = SessionStore(tmp_path).read_manifest(SESSION_KEY)

    assert restored.to_json() == original.to_json()
    assert restored.date_start == race_rows.SESSION_START
    assert restored.schema_version == SCHEMA_VERSION


def test_matches_is_the_idempotency_check(tmp_path):
    """Re-fetching the same race must compare equal, so ingest writes nothing."""
    rows = race_rows.endpoints()
    manifest = race_rows.make_archive(tmp_path, rows).manifest

    assert manifest.matches(rows)

    changed = dict(rows, laps=rows["laps"][:-1])
    assert not manifest.matches(changed)


def test_an_absent_optional_endpoint_is_recorded_as_missing(tmp_path):
    """
    `session_result` and `starting_grid` are newer OpenF1 additions.

    An older race that has neither is still replayable, so the manifest says
    "absent" rather than the ingest failing.
    """
    archive = race_rows.make_archive(
        tmp_path, race_rows.endpoints(session_result=[], starting_grid=[])
    )

    assert archive.manifest.endpoints["session_result"].missing is True
    # A required endpoint is never flagged missing - ingest refuses first.
    assert archive.manifest.endpoints["laps"].missing is False


def test_rows_are_cached_after_the_first_read(tmp_path):
    archive = race_rows.make_archive(tmp_path)

    first = archive.rows("laps")
    (archive.path / "laps.json.gz").unlink()

    assert archive.rows("laps") is first


def test_a_missing_endpoint_file_reads_as_no_rows(tmp_path):
    archive = race_rows.make_archive(tmp_path)
    (archive.path / "overtakes.json.gz").unlink()

    assert archive.rows("overtakes") == []


def test_writing_the_same_rows_twice_is_byte_identical(tmp_path):
    """
    gzip stamps its output with an mtime by default, which would make every
    re-ingest of an unchanged race a git diff. See write_rows.
    """
    rows = race_rows.all_lap_rows()
    first, second = tmp_path / "a.json.gz", tmp_path / "b.json.gz"

    write_rows(first, rows)
    write_rows(second, rows)

    assert first.read_bytes() == second.read_bytes()


def test_loading_an_uncached_session_says_how_to_cache_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="python -m app.ingest --session-key 1234"):
        SessionStore(tmp_path).load(1234)


def test_a_manifest_from_a_newer_schema_is_refused(tmp_path):
    store = SessionStore(tmp_path)
    path = store.manifest_path(SESSION_KEY)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "session_key": SESSION_KEY, "schema_version": SCHEMA_VERSION + 1,
    }), encoding="utf-8")

    # read_manifest downgrades it to "not cached" rather than crashing the app,
    # having logged why.
    assert store.read_manifest(SESSION_KEY) is None
    with pytest.raises(ValueError, match="schema"):
        Manifest.from_json(json.loads(path.read_text(encoding="utf-8")))


def test_manifests_are_listed_oldest_race_first(tmp_path):
    store = SessionStore(tmp_path)
    for key, day in ((9001, 21), (9002, 7), (9003, 14)):
        store.save(
            race_rows.manifest(
                session_key=key,
                date_start=race_rows.SESSION_START.replace(day=day),
            ),
            {"drivers": race_rows.driver_rows()},
        )

    assert store.session_keys() == [9002, 9003, 9001]


def test_listing_ignores_unrelated_directories(tmp_path):
    store = SessionStore(tmp_path)
    store.save(race_rows.manifest(), {"drivers": []})
    (tmp_path / "notes").mkdir()
    (tmp_path / "9999.bak").mkdir()

    assert store.session_keys() == [SESSION_KEY]


def test_listing_an_empty_store_is_not_an_error(tmp_path):
    assert SessionStore(tmp_path / "nothing-here").session_keys() == []


def test_endpoint_record_defaults_survive_an_old_manifest():
    record = EndpointRecord.from_json({"rows": 3, "bytes": 90, "hash": "abc"})

    assert record.missing is False
