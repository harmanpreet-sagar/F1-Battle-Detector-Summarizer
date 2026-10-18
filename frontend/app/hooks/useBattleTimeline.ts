/**
 * Builds the battle timeline from what this page has seen: every battle the
 * poll returns is stretched across the laps it was seen on, and every lap run
 * under SC, VSC or a red flag is recorded.
 *
 * It starts from the lap the viewer tuned in on. The replay broadcast is
 * rebuilt per request from a short window, so the backend has no record of
 * earlier battles to send; whole-race episodes are Phase 2's job. Until then
 * the history is kept in sessionStorage so a reload does not wipe it.
 *
 * A replay loops: when the playlist comes round, the same race (same
 * session_key) starts again from lap 1. A lap lower than the latest one seen
 * means a new run, and the old run's history is dropped rather than drawn
 * over it. The same check discards a stored history from an earlier run when
 * the page is reloaded.
 */
'use client'

import { useEffect, useState } from 'react'
import type { Battle, BattleIntensity, TrackStatus } from '../types/api'

export interface TimelineEpisode {
  key: string
  battleId: string
  chaser: string
  ahead: string
  chaserColour: string | null
  aheadColour: string | null
  firstLap: number
  lastLap: number
  peak: Exclude<BattleIntensity, 'NONE'>
}

export interface Timeline {
  sessionKey: number | null
  startLap: number | null
  /** The highest lap recorded, to tell when the replay has started over. */
  lastLap: number | null
  episodes: TimelineEpisode[]
  neutralisedLaps: number[]
}

/** A pair that drops out for more than this many laps starts a new episode. */
const REJOIN_LAPS = 2
const STORAGE_PREFIX = 'f1bd.timeline.'

const empty = (sessionKey: number | null): Timeline => ({
  sessionKey,
  startLap: null,
  lastLap: null,
  episodes: [],
  neutralisedLaps: [],
})

function load(sessionKey: number | null): Timeline {
  if (sessionKey === null) return empty(null)
  try {
    const raw = window.sessionStorage.getItem(STORAGE_PREFIX + sessionKey)
    if (raw) {
      const parsed = JSON.parse(raw) as Timeline
      if (parsed.sessionKey === sessionKey && Array.isArray(parsed.episodes)) return parsed
    }
  } catch {
    // Storage blocked or corrupt: start fresh.
  }
  return empty(sessionKey)
}

function save(timeline: Timeline) {
  if (timeline.sessionKey === null) return
  try {
    window.sessionStorage.setItem(STORAGE_PREFIX + timeline.sessionKey, JSON.stringify(timeline))
  } catch {
    // Not worth failing the page over.
  }
}

/** Latest lap a timeline has seen. Falls back to its episodes for histories saved before lastLap existed. */
function latestLap(timeline: Timeline): number | null {
  if (timeline.lastLap !== null && timeline.lastLap !== undefined) return timeline.lastLap
  const laps = timeline.episodes.map((e) => e.lastLap)
  if (timeline.startLap !== null) laps.push(timeline.startLap)
  return laps.length ? Math.max(...laps) : null
}

/** True when `lap` is behind what this timeline has already seen: the replay looped. */
export function isNewRun(timeline: Timeline, lap: number): boolean {
  const latest = latestLap(timeline)
  return latest !== null && lap < latest
}

const isNeutral = (s: TrackStatus | null | undefined) => s === 'sc' || s === 'vsc' || s === 'red'

/** Pure update step, exported for tests. */
export function advance(
  timeline: Timeline,
  lap: number,
  battles: Battle[],
  trackStatus: TrackStatus | null | undefined,
): Timeline {
  const episodes = timeline.episodes.map((e) => ({ ...e }))

  for (const b of battles) {
    if (b.intensity === 'NONE') continue
    let current: TimelineEpisode | undefined
    for (let i = episodes.length - 1; i >= 0; i--) {
      if (episodes[i].battleId === b.battle_id) {
        current = episodes[i]
        break
      }
    }

    if (current && lap - current.lastLap <= REJOIN_LAPS && lap >= current.firstLap) {
      current.lastLap = Math.max(current.lastLap, lap)
      if (b.intensity === 'HOT') current.peak = 'HOT'
    } else {
      episodes.push({
        key: `${b.battle_id}@${lap}`,
        battleId: b.battle_id,
        chaser: b.chaser_acronym || String(b.chaser_driver_number),
        ahead: b.ahead_acronym || String(b.ahead_driver_number),
        chaserColour: b.chaser_team_colour ?? null,
        aheadColour: b.ahead_team_colour ?? null,
        firstLap: lap,
        lastLap: lap,
        peak: b.intensity,
      })
    }
  }

  const neutralisedLaps =
    isNeutral(trackStatus) && !timeline.neutralisedLaps.includes(lap)
      ? [...timeline.neutralisedLaps, lap]
      : timeline.neutralisedLaps

  return {
    ...timeline,
    startLap: timeline.startLap ?? lap,
    lastLap: Math.max(latestLap(timeline) ?? lap, lap),
    episodes,
    neutralisedLaps,
  }
}

interface Options {
  sessionKey: number | null | undefined
  lap: number | null | undefined
  battles: Battle[] | undefined
  trackStatus: TrackStatus | null | undefined
}

export function useBattleTimeline({ sessionKey, lap, battles, trackStatus }: Options): Timeline {
  const key = sessionKey ?? null
  const [timeline, setTimeline] = useState<Timeline>(() => empty(key))

  // A new race (the broadcast moved on, or a reload) swaps in its own history.
  useEffect(() => {
    setTimeline(load(key))
  }, [key])

  useEffect(() => {
    if (!lap || !battles) return
    setTimeline((t) => {
      if (t.sessionKey !== key) return t
      // Same race from lap 1 again: start a fresh history, not one drawn over the last run.
      const base = isNewRun(t, lap) ? empty(key) : t
      const next = advance(base, lap, battles, trackStatus)
      save(next)
      return next
    })
  }, [key, lap, battles, trackStatus])

  return timeline
}
