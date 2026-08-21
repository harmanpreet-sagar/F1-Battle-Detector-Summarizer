# F1 Battle Detector — Change List

Review of `F1-Battle-Detector-Summarizer` @ `2bb48a8` (10 commits, Feb 23 – Mar 5 2026).
Backend installed and run against `TEST_MODE`; every endpoint exercised; scoring function probed directly;
OpenF1 field schema verified against the [official docs](https://openf1.org/docs).

**Current state:** architecture is sound and ~1,750 LOC are real. But the detector returns **zero battles
in every mode**, for two independent reasons — and it could never have worked against live data.

**Verdict:** ~2–4 hours of work to make it functional. Worth doing before it goes on a resume.

---

## P0 — Blocking. The app cannot detect a battle today.

### P0-1 · Live mode reads gap data from an endpoint that doesn't return it

`state.py:93-94` reads:

```python
gap_to_leader_s=pos_data.get("gap_to_leader"),
gap_to_ahead_s=pos_data.get("interval"),
```

...from data fetched by `openf1_client.get_latest_positions()`, which hits `openf1_client.py:82` →
the **`position`** endpoint.

Per the OpenF1 docs, `/position` returns exactly: `date`, `driver_number`, `meeting_key`, `position`,
`session_key`. **No `interval`. No `gap_to_leader`.** Those fields live on **`/intervals`**.

So in live mode `gap_to_ahead_s` is `None` for all 20 drivers on every poll, and `detect_battles()`
hits its `if gap is None: continue` guard for every pair. **The app has never detected a battle against
real F1 data and never could have.** TEST_MODE masked this because the mock generator populates gaps directly.

**Fix** — add an intervals method to the client:

```python
# openf1_client.py
async def get_intervals(self, session_key: int) -> List[Dict]:
    """Fetch gap-to-ahead and gap-to-leader. NOTE: /position does not carry these."""
    return await self._request("intervals", {"session_key": session_key})

async def get_latest_intervals(self, session_key: int) -> List[Dict]:
    """Latest interval row per driver."""
    rows = await self.get_intervals(session_key)
    latest = {}
    for r in rows:
        dn = r.get("driver_number")
        if dn is None:
            continue
        if dn not in latest or r["date"] > latest[dn]["date"]:
            latest[dn] = r
    return list(latest.values())
```

Then join intervals onto positions by `driver_number` in the poll loop and pass both into
`update_from_openf1_positions()`.

> ⚠️ `/intervals` updates roughly every 4s while you poll positions at 1.5s. Expect to serve a
> cached interval across ~2–3 position polls. Handle that explicitly rather than letting it look
> like a stalled gap — it will otherwise corrupt your closing-rate calculation.

---

### P0-2 · The WATCH threshold is mathematically unreachable

`battle.py:154`:

```python
pace_delta = None  # "will be implemented when we add lap data"
```

`pace_delta` is never computed, so `pace_score` is permanently `0.0` — zeroing the 20% weight it
carries in `calculate_battle_score()`. What remains caps out around **0.50** under realistic conditions,
and `BATTLE_WATCH_SCORE` is **0.55**.

Measured directly against your scoring function:

| gap (s) | closing rate | score | ≥ 0.55? |
|---|---|---|---|
| **0.00** | none | **0.500** | ❌ |
| 0.00 | −0.004 | 0.512 | ❌ |
| 0.20 | −0.02 | 0.527 | ❌ |
| 0.35 | −0.004 | 0.454 | ❌ |
| 0.00 | −0.02 | 0.560 | ✅ |

**Two cars touching bumpers at a 0.0s gap score 0.500 and are classified `NONE`.** Detection only fires
at a closing rate ≤ −0.02 s/s — 1.2s of gap eaten per minute — which is rare outside a genuine lunge.

**Fix — pick one:**

**(a) Compute `pace_delta` (preferred).** You already fetch lap data via `get_laps()` and never use it.
This is the term that makes the score meaningful — a car that's 0.3s behind *and half a second a lap
faster* is the actual definition of a battle.

```python
def calculate_pace_delta(chaser_hist, ahead_hist, n=3) -> Optional[float]:
    """Mean lap-time delta over last n laps. Negative = chaser is faster."""
    c = [s.last_lap_time_s for s in chaser_hist if s.last_lap_time_s][-n:]
    a = [s.last_lap_time_s for s in ahead_hist  if s.last_lap_time_s][-n:]
    if not c or not a:
        return None
    return (sum(c)/len(c)) - (sum(a)/len(a))
```

Requires populating `last_lap_time_s`, currently hardcoded `None` at `state.py:92`.

**(b) Recalibrate thresholds.** Faster, weaker. `BATTLE_WATCH_SCORE=0.40`, `BATTLE_HOT_SCORE=0.55`.
Do this only as a stopgap — it leaves 20% of your scoring function dead.

Whichever you choose, **write a test that asserts a 0.3s gap with a closing rate produces `WATCH`.**
The absence of that test is why this survived 10 commits.

---

## P1 — Correctness bugs that will produce visibly wrong output

### P1-1 · Detection runs in the request handler, so the stability filter counts HTTP requests

`main.py:250` calls `detect_battles()` inside `GET /battles/top`. The module-global `_battle_tracker`
in `battle.py:16` is therefore mutated **per HTTP request**, not per data poll.

Consequences:

- `duration_updates` measures *how many times a client asked*, not how long the battle has lasted. `BATTLE_MIN_DURATION_UPDATES = 3` means "three requests," which is meaningless.
- Two browser tabs double the increment rate. Zero clients means battles never mature.
- Concurrent requests race on shared mutable global state.
- Every client re-runs full detection over 20 drivers.

**Fix:** move detection into `poll_positions()` — run it once per poll, cache the result, and make
`/battles/top` a pure read of that cache. This is a small refactor and it fixes the stability filter,
the race, and the duplicated work in one move.

---

### P1-2 · Battles are evicted after 30s of *existing*, not 30s of *absence*

`battle.py:233-252`. On re-detection you preserve the original `first_seen`:

```python
_battle_tracker[battle_id] = (battle, first_seen)   # first_seen never refreshed
```

then evict on it:

```python
cutoff_time = current_time.timestamp() - 30
_battle_tracker = {bid: (b, t) for bid, (b, t) in _battle_tracker.items()
                   if t.timestamp() > cutoff_time}
```

So a battle running continuously for over 30 seconds is dropped from the tracker, resets
`duration_updates` to 1, and vanishes from the UI for 3 polls before reappearing. **The longest,
most interesting battles flicker the most** — the exact opposite of the intent.

**Fix:** store `last_seen` and refresh it on every re-detection; evict on that.

```python
_battle_tracker[battle_id] = (battle, first_seen, current_time)   # first_seen, last_seen
...
_battle_tracker = {bid: v for bid, v in _battle_tracker.items()
                   if v[2].timestamp() > cutoff_time}
```

---

### P1-3 · Closing rate assumes perfectly regular polling

`battle.py:87`:

```python
time_span = (last_idx - first_idx) * config.POLL_POSITIONS_INTERVAL_S
```

Sample spacing is inferred from a config constant rather than measured. Any retry backoff, slow
response, or `/intervals` lagging behind `/position` silently corrupts the rate — and since closing
rate drives 30% of the score, errors show up directly as wrong intensities.

**Fix:** use the real timestamps you're already storing.

```python
time_span = (recent[last_idx].updated_at - recent[first_idx].updated_at).total_seconds()
if time_span <= 0:
    return None
```

---

## P2 — Dead and incorrect logic

### P2-1 · `blue_flag_situation` can never be true

`battle.py:160`:

```python
blue_flag_situation=abs(ahead.position - chaser.position) > 5,
```

`chaser` and `ahead` are **adjacent** in the position-sorted list, so this difference is always 1.
The condition is unreachable. Blue flags concern *lapped* cars, which needs a lap-count comparison,
not a position delta. Either implement it against lap data or delete the field.

### P2-2 · `StateManager.detect_pit_windows()` is a no-op

`state.py:104-126` computes a gap delta, logs it, and comments *"We'll use this information in battle
detection"* — but sets no flag and returns nothing. It's called every poll from `main.py:79` and does
nothing. Meanwhile `battle.detect_pit_window()` is a **second, separate implementation** that is
actually used. Delete the dead one or make it the single source of truth.

### P2-3 · History depth silently caps the trend endpoint

`state.py:21` sets `max_history_length = config.BATTLE_GAP_TREND_WINDOW` (**6**). But
`GET /drivers/{n}/trend` defaults to `points=10` and can never return more than 6. At a 1.5s poll
that's 9 seconds of history — thin for a closing-rate regression and thin for a sparkline.

**Fix:** decouple them. `HISTORY_MAX_LEN = 60` (~90s) with the trend window as a separate read-slice.
Memory cost is trivial: 20 drivers × 60 states.

### P2-4 · State is never reset between sessions

`StateManager.clear()` exists and is never called; `_battle_tracker` has no reset at all. Across a
session change you'll carry stale driver states and battle IDs. Call both when `session_key` changes.

---

## P3 — Hygiene

| Item | Detail |
|---|---|
| **README is stale** | Still says *"starter skeleton with stub implementations"* and lists battle detection as 🚧. It's substantially built. This undersells the project to anyone who opens the repo — including recruiters. |
| **Tests are stubs** | 4 of 6 pass; the 2 failures are missing `pytest-asyncio`, and 4 of the 6 test bodies are `# TODO`. Add it to `requirements-dev.txt`. |
| **No scoring tests** | The P0-2 bug is exactly what a table-driven test over `calculate_battle_score` would have caught on day one. Highest-value test to write. |
| **Stray file** | `backend/.env~` (editor backup) is in the working tree. Gitignored, but delete it. |
| **`Watchlist.tsx`** | 18 lines, `// TODO: Implement watchlist state management`. Either build it or remove it from the README feature list — right now the README promises a feature that renders nothing. |

---

## Suggested order

1. **P0-1** — `/intervals` client + join. *Without this nothing else matters.*
2. **P0-2(a)** — compute `pace_delta`; populate `last_lap_time_s`.
3. Write the scoring test table. Confirm a 0.3s gap + closing rate → `WATCH`.
4. **P1-1** — move detection into the poll loop, cache it.
5. **P1-2**, **P1-3** — `last_seen` eviction, real timestamps.
6. **P2** cleanup, then rewrite the README to describe what it actually does.

Steps 1–3 get it working. Steps 4–6 make it something you'd want reviewed in an interview.

---

## After it works: the ML upgrade

This is the highest-value item in your entire portfolio, because it converts your weakest track into
a real one.

Your weights (`0.5 / 0.3 / 0.2`) and thresholds (`0.55 / 0.70`) are hand-picked constants. Replace
them with learned ones:

1. **Label data.** Pull a season of OpenF1 sessions. Label battle windows — overtake attempts from lap-position changes give you weak labels cheaply, or hand-label a few races.
2. **Featurize.** You already compute gap, closing rate, pace delta, tire age, track status. That's your feature vector, already engineered.
3. **Train.** Logistic regression or gradient boosting. Small data, interpretable, appropriate.
4. **Benchmark against the heuristic.** Precision/recall on held-out races, split temporally — never randomly, since consecutive polls leak.
5. **Ship both.** Keep the heuristic as the baseline and serve the model behind a flag.

The resulting bullet — *"built a rule-based baseline, then trained a classifier that improved battle-detection
precision from X to Y on held-out races"* — is stronger than most standalone ML projects, because it
demonstrates a baseline, an evaluation protocol, and a leakage-aware split. That's what distinguishes
someone who has done ML from someone who has called `.fit()`.

You'd also be able to honestly list ML on your resume, which today you cannot.
