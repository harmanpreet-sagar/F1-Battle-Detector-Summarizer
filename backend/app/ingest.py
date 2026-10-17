"""
Fetch one race from OpenF1 and cache it on disk, once.

    python -m app.ingest --year 2025 --round 14
    python -m app.ingest --session-key 9693
    python -m app.ingest --year 2025 --round 14 --dry-run

Historical OpenF1 data is free and unauthenticated, but it publishes no
rate limit, so the strategy is simply to be polite and to fetch each race
exactly once: requests are sequential with a pause between them, retries back
off, and a re-run whose freshly fetched rows hash equal to what is stored
writes nothing.

The run prints rows and compressed bytes per endpoint. Those numbers decide
how many races can ship inside the serverless bundle (1.7), so they are the
output of this step, not a debugging aid.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import argparse
import asyncio
import logging
import sys

from app.clock import utcnow
from app.config import config
from app.openf1_client import OpenF1APIError, openf1_client
from app.replay.store import ENDPOINTS, Manifest, SessionStore
from app.state import parse_openf1_timestamp

logger = logging.getLogger(__name__)


class IngestError(Exception):
    """Ingest refused or could not complete. The message is for a human."""


@dataclass
class IngestResult:
    """What one run did, for printing and for tests to assert on."""
    manifest: Manifest
    fetched: bool          # False when the cache already matched
    dry_run: bool = False

    @property
    def session_key(self) -> int:
        return self.manifest.session_key


def _session_label(session: Dict) -> str:
    return (
        f"{session.get('year')} {session.get('circuit_short_name')} "
        f"{session.get('session_name')} (key {session.get('session_key')})"
    )


class RaceIngestor:
    """
    Resolves a race, fetches its endpoints and hands them to the store.

    The client, store and clock are injected so the whole thing can be tested
    without a network: `tests/test_ingest.py` drives it with a stub client.
    """

    def __init__(self, client=None, store: Optional[SessionStore] = None,
                 now=utcnow, pause_s: Optional[float] = None):
        self._client = client or openf1_client
        self._store = store or SessionStore()
        self._now = now
        self._pause_s = config.INGEST_REQUEST_PAUSE_S if pause_s is None else pause_s

    # -- resolving which race to fetch -----------------------------------

    async def resolve_session(self, session_key: Optional[int] = None,
                              year: Optional[int] = None,
                              round_number: Optional[int] = None,
                              session_name: str = "Race") -> Dict:
        """
        Find the session row for `--session-key` or for `--year/--round`.

        OpenF1 carries no round number, so a round is defined here as the Nth
        race of that season by start date. That matches the championship's own
        numbering for a normal calendar and, unlike counting meetings, is not
        thrown off by pre-season testing, which has no race session.
        """
        if session_key is not None:
            sessions = await self._client.get_endpoint(
                "sessions", {"session_key": session_key}
            )
            if not sessions:
                raise IngestError(f"OpenF1 knows no session {session_key}")
            return sessions[0]

        if year is None or round_number is None:
            raise IngestError("pass --session-key, or both --year and --round")

        sessions = await self._client.get_endpoint(
            "sessions", {"year": year, "session_name": session_name}
        )
        if not sessions:
            raise IngestError(f"no {session_name} sessions found for {year}")

        ordered = sorted(sessions, key=lambda s: s.get("date_start") or "")
        if not 1 <= round_number <= len(ordered):
            raise IngestError(
                f"{year} has {len(ordered)} rounds with a {session_name} session; "
                f"--round {round_number} is out of range"
            )
        return ordered[round_number - 1]

    async def resolve_meeting_name(self, session: Dict) -> Optional[str]:
        """
        The Grand Prix's name, e.g. "Hungarian Grand Prix".

        `sessions` rows carry the circuit but not the meeting, so the name
        comes from `/meetings`: one more request per race, made only when the
        session row lacks it. The short `meeting_name` is preferred over
        `meeting_official_name` ("FORMULA 1 ... HUNGARIAN GRAND PRIX 2025"),
        which is the sponsor's title rather than something a header can show.
        A failure costs only the name: the race is replayable without it.
        """
        name = session.get("meeting_name") or session.get("meeting_official_name")
        if name or session.get("meeting_key") is None:
            return name
        try:
            meetings = await self._client.get_endpoint(
                "meetings", {"meeting_key": session["meeting_key"]}
            )
        except OpenF1APIError as exc:
            logger.warning("Meeting name unavailable: %s", exc)
            return None
        if not meetings:
            return None
        return meetings[0].get("meeting_name") or meetings[0].get("meeting_official_name")

    # -- the free-tier guard ---------------------------------------------

    def check_is_old_enough(self, session: Dict) -> None:
        """
        Refuse a session that may still be inside OpenF1's live window.

        Live timing is a paid tier and the free archive only settles once the
        session is over. Fetching too early either fails or caches a partial
        race that looks complete, so the buffer is enforced rather than
        suggested.
        """
        date_end = parse_openf1_timestamp(session.get("date_end"))
        if date_end is None:
            logger.warning(
                "Session %s has no date_end; cannot check the live window",
                session.get("session_key"),
            )
            return

        earliest = date_end + timedelta(minutes=config.INGEST_MIN_SESSION_AGE_MIN)
        now = self._now()
        if now < earliest:
            wait = (earliest - now).total_seconds() / 60
            raise IngestError(
                f"{_session_label(session)} ended at {date_end:%Y-%m-%d %H:%M} UTC. "
                f"OpenF1's free archive settles "
                f"{config.INGEST_MIN_SESSION_AGE_MIN:.0f} minutes after that - "
                f"try again in {wait:.0f} minutes."
            )

    # -- fetching ---------------------------------------------------------

    async def fetch_endpoints(self, session_key: int) -> Dict[str, List[Dict]]:
        """
        Pull every endpoint for one session, sequentially and politely.

        An optional endpoint that errors or is empty is recorded as absent:
        `session_result` and `starting_grid` are recent OpenF1 additions and
        are missing for older races, which is not a reason to lose the race.
        """
        fetched: Dict[str, List[Dict]] = {}

        for index, (endpoint, required) in enumerate(ENDPOINTS.items()):
            if index and self._pause_s:
                await asyncio.sleep(self._pause_s)
            try:
                rows = await self._client.get_endpoint(
                    endpoint, {"session_key": session_key}
                )
            except OpenF1APIError as exc:
                if required:
                    raise IngestError(f"/{endpoint} failed: {exc}") from exc
                logger.warning("Optional endpoint /%s unavailable: %s", endpoint, exc)
                rows = []

            if required and not rows:
                raise IngestError(
                    f"/{endpoint} returned no rows for session {session_key}; "
                    f"the session is not replayable without it"
                )

            fetched[endpoint] = rows
            logger.info("  /%-15s %6d rows", endpoint, len(rows))

        return fetched

    # -- the run ----------------------------------------------------------

    async def run(self, session_key: Optional[int] = None,
                  year: Optional[int] = None,
                  round_number: Optional[int] = None,
                  session_name: str = "Race",
                  force: bool = False,
                  dry_run: bool = False) -> IngestResult:
        session = await self.resolve_session(
            session_key=session_key, year=year,
            round_number=round_number, session_name=session_name,
        )
        key = session["session_key"]
        logger.info("Ingesting %s", _session_label(session))

        self.check_is_old_enough(session)

        existing = self._store.read_manifest(key)
        if existing is not None and not force:
            # Cheap check first: a complete manifest means the race is already
            # here, and confirming that by re-fetching every endpoint would
            # defeat the point of fetching each race once.
            if all(name in existing.endpoints for name in ENDPOINTS):
                logger.info(
                    "Already cached (%d rows, %.1f MB). Use --force to refetch.",
                    existing.total_rows, existing.total_bytes / 1e6,
                )
                if not existing.meeting_name and not dry_run:
                    # Races cached before ingest looked the name up. Filling
                    # it in is one request and rewrites only the manifest.
                    existing.meeting_name = await self.resolve_meeting_name(session)
                    if existing.meeting_name:
                        self._store.write_manifest(existing)
                        logger.info("Backfilled meeting name: %s", existing.meeting_name)
                return IngestResult(manifest=existing, fetched=False)
            logger.info("Cache is incomplete; refetching.")

        if dry_run:
            logger.info("Dry run: resolved the session, fetched nothing.")
            return IngestResult(
                manifest=self._manifest_for(session, year, round_number),
                fetched=False,
                dry_run=True,
            )

        fetched = await self.fetch_endpoints(key)

        if existing is not None and existing.matches(fetched):
            logger.info("Fetched rows are identical to the cache; nothing written.")
            return IngestResult(manifest=existing, fetched=False)

        manifest = self._manifest_for(session, year, round_number)
        manifest.meeting_name = await self.resolve_meeting_name(session)
        manifest = self._store.save(manifest, fetched)
        return IngestResult(manifest=manifest, fetched=True)

    def _manifest_for(self, session: Dict, year: Optional[int],
                      round_number: Optional[int]) -> Manifest:
        return Manifest(
            session_key=session["session_key"],
            year=session.get("year") or year,
            round=round_number,
            circuit=session.get("circuit_short_name"),
            meeting_name=session.get("meeting_name") or session.get("meeting_official_name"),
            session_name=session.get("session_name"),
            date_start=parse_openf1_timestamp(session.get("date_start")),
            date_end=parse_openf1_timestamp(session.get("date_end")),
            gmt_offset=session.get("gmt_offset") or "+00:00",
            fetched_at=self._now(),
        )


def format_report(manifest: Manifest) -> str:
    """
    The measurement asked for in 1.1: rows and compressed bytes per endpoint.

    Printed on every run, including a run that wrote nothing, because the
    question it answers - how much of the bundle budget a race costs - is
    asked long after the fetch.
    """
    lines = [
        "",
        f"  {manifest.year} {manifest.circuit} {manifest.session_name} "
        f"(session_key {manifest.session_key})",
        f"  {'endpoint':<16}{'rows':>9}{'size':>11}",
        f"  {'-' * 36}",
    ]
    for name in ENDPOINTS:
        record = manifest.endpoints.get(name)
        if record is None:
            lines.append(f"  {name:<16}{'-':>9}{'not fetched':>11}")
            continue
        size = "absent" if record.missing else f"{record.bytes / 1024:.0f} KB"
        lines.append(f"  {name:<16}{record.rows:>9,}{size:>11}")
    lines.append(f"  {'-' * 36}")
    lines.append(
        f"  {'total':<16}{manifest.total_rows:>9,}"
        f"{manifest.total_bytes / 1e6:>8.1f} MB"
    )
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest",
        description="Cache one race from OpenF1 for replay.",
    )
    parser.add_argument("--year", type=int, help="season, e.g. 2025")
    parser.add_argument(
        "--round", type=int, dest="round_number",
        help="Nth race of that season by date, e.g. 14",
    )
    parser.add_argument("--session-key", type=int, help="OpenF1 session_key")
    parser.add_argument(
        "--session-name", default="Race",
        help="which session of the weekend (default: Race)",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help=f"where to write (default: {config.REPLAY_DATA_DIR})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="refetch even when the session is already cached",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="resolve the session and stop, without fetching its data",
    )
    parser.add_argument("--list", action="store_true", help="list cached races and exit")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def print_cached(store: SessionStore) -> None:
    manifests = store.manifests()
    if not manifests:
        print(f"No cached races under {store.root}")
        return
    print(f"{len(manifests)} cached race(s) under {store.root}:")
    total = 0
    for manifest in manifests:
        total += manifest.total_bytes
        start = f"{manifest.date_start:%Y-%m-%d}" if manifest.date_start else "?"
        print(
            f"  {manifest.session_key:>7}  {start}  "
            f"{(manifest.circuit or '?'):<16}{manifest.total_bytes / 1e6:>6.1f} MB"
        )
    print(f"  {'total':>7}{'':<28}{total / 1e6:>6.1f} MB")


async def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    store = SessionStore(args.data_dir)

    if args.list:
        print_cached(store)
        return 0

    ingestor = RaceIngestor(store=store)
    try:
        result = await ingestor.run(
            session_key=args.session_key,
            year=args.year,
            round_number=args.round_number,
            session_name=args.session_name,
            force=args.force,
            dry_run=args.dry_run,
        )
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OpenF1APIError as exc:
        # Reaching OpenF1 at all can fail: no network, or a TLS-intercepting
        # proxy whose certificate the default bundle does not trust. Neither
        # is a bug worth a traceback, and the second has a fix worth naming.
        print(f"error: could not reach OpenF1: {exc}", file=sys.stderr)
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            print(
                "hint: something is intercepting TLS on this machine. Point "
                "OPENF1_CA_BUNDLE at its CA certificate.",
                file=sys.stderr,
            )
        return 1
    finally:
        await openf1_client.close()

    if result.dry_run:
        print(f"would fetch session {result.session_key}")
        return 0

    print(format_report(result.manifest))
    if result.fetched:
        print(f"Wrote {store.path_for(result.session_key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
