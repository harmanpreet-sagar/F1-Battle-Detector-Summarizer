"""
Tests for scheduling the broadcast from manifests.

A cold instance lays out the playlist from each race's stored playback
summary and indexes only the race on air. That is right only while the stored
summaries match what the timeline code computes, which the first test checks
for every shipped race.
"""
from datetime import timedelta

from app.config import config
from app.ingest import refresh_playback
from app.replay.broadcast import Broadcast
from app.replay.engine import RaceReplay
from app.replay.store import Manifest, SessionStore
from app.replay.timeline import playback_summary
from tests import race_rows


def test_every_shipped_race_stores_the_summary_its_timeline_gives():
    """
    If this fails, the timeline code changed under the cache. Run
    `python -m app.ingest --refresh-playback` and commit the manifests.
    """
    store = SessionStore()
    for manifest in store.manifests():
        assert manifest.playback == playback_summary(store.load(manifest.session_key)), \
            manifest.session_key


def test_a_summary_survives_the_manifest_round_trip():
    manifest = SessionStore().manifests()[0]

    assert manifest.playback is not None
    assert Manifest.from_json(manifest.to_json()).playback == manifest.playback


def test_scheduling_from_manifests_matches_scheduling_from_timelines():
    store = SessionStore()
    keys = store.session_keys()
    lazy = Broadcast.from_store(store, playlist=keys)
    eager = Broadcast([RaceReplay(store.load(key)) for key in keys], epoch=lazy.epoch)

    assert lazy.duration_s == eager.duration_s
    step = lazy.duration_s / 500
    for i in range(500):
        moment = lazy.epoch + timedelta(seconds=i * step + 0.7)
        assert lazy.locate(moment) == eager.locate(moment)


def test_a_cold_broadcast_indexes_only_the_race_on_air():
    broadcast = Broadcast.from_store(SessionStore(), playlist=[])
    assert not any(slot.loaded for slot in broadcast.slots)

    moment = broadcast.epoch + timedelta(seconds=broadcast.duration_s / 3)
    broadcast.status_at(moment)
    broadcast.session_at(moment)
    broadcast.build_pipeline(moment)

    assert [slot.loaded for slot in broadcast.slots].count(True) == 1


def test_a_race_without_a_summary_is_indexed_up_front(tmp_path):
    store = SessionStore(tmp_path)
    store.save(race_rows.manifest(), race_rows.endpoints())

    broadcast = Broadcast.from_store(store, playlist=[race_rows.SESSION_KEY])

    assert broadcast.slots[0].loaded


def test_a_summary_for_other_settings_is_not_trusted(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    store.save(race_rows.manifest(), race_rows.endpoints())
    refresh_playback(store, race_rows.SESSION_KEY)
    assert not Broadcast.from_store(store, playlist=[race_rows.SESSION_KEY]).slots[0].loaded

    monkeypatch.setattr(config, "REPLAY_QUIET_GAP_S", config.REPLAY_QUIET_GAP_S + 30)

    assert Broadcast.from_store(store, playlist=[race_rows.SESSION_KEY]).slots[0].loaded


def test_refreshing_writes_the_manifest_once(tmp_path):
    store = SessionStore(tmp_path)
    store.save(race_rows.manifest(), race_rows.endpoints())

    _, changed = refresh_playback(store, race_rows.SESSION_KEY)
    _, again = refresh_playback(store, race_rows.SESSION_KEY)

    assert (changed, again) == (True, False)
    assert store.read_manifest(race_rows.SESSION_KEY).playback is not None
