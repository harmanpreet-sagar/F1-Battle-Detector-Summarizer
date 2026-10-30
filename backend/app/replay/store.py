"""
The on-disk race cache: `data/sessions/{session_key}/{endpoint}.json.gz`.

One race is fetched once, ever. `app/ingest.py` writes here, the timeline
builder and ReplaySource read from here, and the deployed app ships a curated
handful of these directories inside its bundle so that serving a replay never
touches OpenF1 (1.7).

Every directory carries a `manifest.json` describing what was fetched: row
counts and a content hash per endpoint, plus a schema version. The hashes are
what make re-running ingest a no-op instead of a second round of requests.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
import gzip
import hashlib
import json
import logging

from app.config import config
from app.state import parse_openf1_timestamp

logger = logging.getLogger(__name__)

# Bumped whenever the stored shape changes in a way a reader must notice.
# Readers refuse a newer version rather than silently misreading it.
SCHEMA_VERSION = 1

# Fetched per race, in this order. `required` endpoints make a session
# unusable by their absence; the rest are Phase 2 material or newer OpenF1
# additions that are missing for some sessions, and a 404 on one of those must
# not cost us the whole race.
#
# `car_data` and `location` are deliberately absent: ~3.7 Hz telemetry for 20
# cars is orders of magnitude larger than everything here combined, and
# nothing in Phases 1-3 reads it.
ENDPOINTS: Dict[str, bool] = {
    "session_result": False,  # final classification, DNF/DSQ (Phase 2)
    "drivers": True,          # names, teams, acronyms
    "position": True,         # running order over time
    "intervals": True,        # gap to car ahead, gap to leader
    "laps": True,             # lap and sector times
    "race_control": True,     # safety car, VSC, flags (fixes B4)
    "stints": False,          # compound and tyre age (fixes B5)
    "pit": False,             # pit laps and lane duration (fixes B5)
    "overtakes": False,       # official-ish pass list (Phase 2)
    "starting_grid": False,   # grid order for the lap-1 baseline
}

REQUIRED_ENDPOINTS = tuple(name for name, required in ENDPOINTS.items() if required)


def content_hash(rows: List[Dict]) -> str:
    """
    A stable digest of one endpoint's rows.

    Hashing the parsed rows rather than the compressed bytes keeps the digest
    independent of gzip's timestamp and compression level, so the same data
    fetched twice compares equal and ingest stays idempotent.
    """
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class EndpointRecord:
    """What the manifest remembers about one fetched endpoint."""
    rows: int
    bytes: int          # compressed size on disk
    hash: str
    missing: bool = False   # endpoint is absent for this session

    def to_json(self) -> Dict:
        return {
            "rows": self.rows,
            "bytes": self.bytes,
            "hash": self.hash,
            "missing": self.missing,
        }

    @classmethod
    def from_json(cls, data: Dict) -> "EndpointRecord":
        return cls(
            rows=data.get("rows", 0),
            bytes=data.get("bytes", 0),
            hash=data.get("hash", ""),
            missing=data.get("missing", False),
        )


@dataclass
class PlaybackSummary:
    """
    What the broadcast schedule needs from a race: when it starts, when it
    ends and where the feed goes quiet (red flags).

    Ingest stores it so a cold instance can lay out the whole playlist from
    manifests and index only the race on air. It depends on two settings,
    kept alongside so a reader can tell when it no longer applies.
    """
    start: datetime
    end: datetime
    quiet_periods: List[Tuple[datetime, datetime]]
    quiet_gap_s: float
    retirement_laps: int

    def applies(self, quiet_gap_s: float, retirement_laps: int) -> bool:
        return self.quiet_gap_s == quiet_gap_s and self.retirement_laps == retirement_laps

    def to_json(self) -> Dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "quiet_periods": [[a.isoformat(), b.isoformat()] for a, b in self.quiet_periods],
            "quiet_gap_s": self.quiet_gap_s,
            "retirement_laps": self.retirement_laps,
        }

    @classmethod
    def from_json(cls, data: Dict) -> "PlaybackSummary":
        return cls(
            start=parse_openf1_timestamp(data["start"]),
            end=parse_openf1_timestamp(data["end"]),
            quiet_periods=[
                (parse_openf1_timestamp(a), parse_openf1_timestamp(b))
                for a, b in data.get("quiet_periods", [])
            ],
            quiet_gap_s=float(data["quiet_gap_s"]),
            retirement_laps=int(data["retirement_laps"]),
        )


@dataclass
class Manifest:
    """The identity and provenance of one cached session."""
    session_key: int
    year: Optional[int] = None
    round: Optional[int] = None
    circuit: Optional[str] = None
    meeting_name: Optional[str] = None
    session_name: Optional[str] = None
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    gmt_offset: str = "+00:00"
    fetched_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION
    endpoints: Dict[str, EndpointRecord] = field(default_factory=dict)
    playback: Optional[PlaybackSummary] = None

    @property
    def total_bytes(self) -> int:
        return sum(record.bytes for record in self.endpoints.values())

    @property
    def total_rows(self) -> int:
        return sum(record.rows for record in self.endpoints.values())

    def matches(self, fetched: Dict[str, List[Dict]]) -> bool:
        """True when freshly fetched rows are exactly what is already stored."""
        return all(
            name in self.endpoints and self.endpoints[name].hash == content_hash(rows)
            for name, rows in fetched.items()
        )

    def to_json(self) -> Dict:
        return {
            "schema_version": self.schema_version,
            "session_key": self.session_key,
            "year": self.year,
            "round": self.round,
            "circuit": self.circuit,
            "meeting_name": self.meeting_name,
            "session_name": self.session_name,
            "date_start": self.date_start.isoformat() if self.date_start else None,
            "date_end": self.date_end.isoformat() if self.date_end else None,
            "gmt_offset": self.gmt_offset,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "endpoints": {n: r.to_json() for n, r in self.endpoints.items()},
            **({"playback": self.playback.to_json()} if self.playback else {}),
        }

    @classmethod
    def from_json(cls, data: Dict) -> "Manifest":
        version = data.get("schema_version", 0)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"cached session {data.get('session_key')} was written by schema "
                f"v{version}; this build reads up to v{SCHEMA_VERSION}"
            )
        return cls(
            session_key=data["session_key"],
            year=data.get("year"),
            round=data.get("round"),
            circuit=data.get("circuit"),
            meeting_name=data.get("meeting_name"),
            session_name=data.get("session_name"),
            date_start=parse_openf1_timestamp(data.get("date_start")),
            date_end=parse_openf1_timestamp(data.get("date_end")),
            gmt_offset=data.get("gmt_offset", "+00:00"),
            fetched_at=parse_openf1_timestamp(data.get("fetched_at")),
            schema_version=version,
            endpoints={
                name: EndpointRecord.from_json(record)
                for name, record in (data.get("endpoints") or {}).items()
            },
            playback=(
                PlaybackSummary.from_json(data["playback"]) if data.get("playback") else None
            ),
        )


def read_rows(path: Path) -> List[Dict]:
    """Read one gzipped endpoint file. A missing file is an empty endpoint."""
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def write_rows(path: Path, rows: List[Dict]) -> int:
    """
    Write one endpoint, compressed. Returns the size on disk.

    gzip stamps its header with an mtime and the source filename, both of
    which would make the bytes differ between two writes of identical data and
    so make every re-ingest of an unchanged race a git diff. Pinning both
    keeps the file a function of its contents alone.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, separators=(",", ":"), default=str).encode("utf-8")
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=9, mtime=0) as handle:
            handle.write(payload)
    return path.stat().st_size


class SessionArchive:
    """
    One cached race, read lazily.

    Rows are decompressed on first access and then kept, because a warm
    serverless instance answers many requests from one loaded race and
    re-reading the whole of `intervals` per request would dominate the
    response time (1.7).
    """

    def __init__(self, path: Path, manifest: Manifest):
        self.path = path
        self.manifest = manifest
        self._cache: Dict[str, List[Dict]] = {}

    @property
    def session_key(self) -> int:
        return self.manifest.session_key

    def rows(self, endpoint: str) -> List[Dict]:
        """Rows for one endpoint. An endpoint absent for this race gives []."""
        if endpoint not in self._cache:
            self._cache[endpoint] = read_rows(self.path / f"{endpoint}.json.gz")
        return self._cache[endpoint]

    def load_all(self) -> None:
        """Decompress every endpoint now, so later reads cost nothing."""
        for endpoint in ENDPOINTS:
            self.rows(endpoint)

    def __repr__(self) -> str:
        m = self.manifest
        return f"<SessionArchive {m.session_key} {m.year} {m.circuit}>"


class SessionStore:
    """The `data/sessions/` directory: find, read and write cached races."""

    def __init__(self, root: Optional[Path] = None):
        # A relative path resolves against backend/, so the cache is found no
        # matter which directory the process was started from.
        configured = Path(root) if root is not None else Path(config.REPLAY_DATA_DIR)
        if not configured.is_absolute():
            configured = Path(__file__).resolve().parents[2] / configured
        self.root = configured

    def path_for(self, session_key: int) -> Path:
        return self.root / str(session_key)

    def manifest_path(self, session_key: int) -> Path:
        return self.path_for(session_key) / "manifest.json"

    def has(self, session_key: int) -> bool:
        return self.manifest_path(session_key).exists()

    def read_manifest(self, session_key: int) -> Optional[Manifest]:
        path = self.manifest_path(session_key)
        if not path.exists():
            return None
        try:
            return Manifest.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.warning(f"Unreadable manifest at {path}: {exc}")
            return None

    def write_manifest(self, manifest: Manifest) -> None:
        path = self.manifest_path(manifest.session_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest.to_json(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def save(self, manifest: Manifest, fetched: Dict[str, List[Dict]]) -> Manifest:
        """Write every endpoint plus the manifest that describes them."""
        directory = self.path_for(manifest.session_key)
        for endpoint, rows in fetched.items():
            size = write_rows(directory / f"{endpoint}.json.gz", rows)
            manifest.endpoints[endpoint] = EndpointRecord(
                rows=len(rows),
                bytes=size,
                hash=content_hash(rows),
                missing=not rows and not ENDPOINTS.get(endpoint, True),
            )
        manifest.schema_version = SCHEMA_VERSION
        self.write_manifest(manifest)
        return manifest

    def load(self, session_key: int) -> SessionArchive:
        """Open a cached session for reading."""
        manifest = self.read_manifest(session_key)
        if manifest is None:
            raise FileNotFoundError(
                f"no cached session {session_key} under {self.root}; "
                f"run `python -m app.ingest --session-key {session_key}`"
            )
        return SessionArchive(self.path_for(session_key), manifest)

    def manifests(self) -> List[Manifest]:
        """Every readable cached session, oldest race first."""
        if not self.root.exists():
            return []
        found = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or not directory.name.isdigit():
                continue
            manifest = self.read_manifest(int(directory.name))
            if manifest is not None:
                found.append(manifest)
        # Undated manifests sort last rather than crashing the comparison.
        far_future = datetime.max.replace(tzinfo=timezone.utc)
        found.sort(key=lambda m: (m.date_start or far_future, m.session_key))
        return found

    def session_keys(self) -> List[int]:
        return [manifest.session_key for manifest in self.manifests()]
