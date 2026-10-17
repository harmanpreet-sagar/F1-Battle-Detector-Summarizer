/**
 * Every battle seen so far as a bar across the laps it ran, with neutralised
 * laps shaded and a playhead at the current lap. Borrowed from the Pit Wall
 * direction; the shape the Phase 2 race report will reuse.
 *
 * One row per pair. A pair that fights, drops back and fights again gets
 * several segments on its row, not a new row each time, so the rows stay a
 * list of who fought rather than a list of every time the detector blinked.
 */
'use client'

import { useState } from 'react'
import type { Timeline, TimelineEpisode } from '../hooks/useBattleTimeline'
import { teamColour } from '../lib/format'
import { lapRuns } from './ReplayHeader'

interface BattleTimelineProps {
  timeline: Timeline
  currentLap: number | null | undefined
  totalLaps: number | null | undefined
  activeIds: Set<string>
  /** Pairs shown before "Show all"; the most recently active win. */
  collapsedPairs?: number
}

const LABEL_W = 132

export interface PairLane {
  battleId: string
  chaser: string
  ahead: string
  chaserColour: string | null
  aheadColour: string | null
  firstLap: number
  lastLap: number
  /** In lap order. */
  segments: TimelineEpisode[]
}

/** Group episodes into one lane per pair, ordered by when the pair first fought. */
export function pairLanes(episodes: TimelineEpisode[]): PairLane[] {
  const byPair = new Map<string, PairLane>()
  for (const e of [...episodes].sort((a, b) => a.firstLap - b.firstLap)) {
    const lane = byPair.get(e.battleId)
    if (lane) {
      lane.segments.push(e)
      lane.lastLap = Math.max(lane.lastLap, e.lastLap)
    } else {
      byPair.set(e.battleId, {
        battleId: e.battleId,
        chaser: e.chaser,
        ahead: e.ahead,
        chaserColour: e.chaserColour,
        aheadColour: e.aheadColour,
        firstLap: e.firstLap,
        lastLap: e.lastLap,
        segments: [e],
      })
    }
  }
  return Array.from(byPair.values()).sort((a, b) => a.firstLap - b.firstLap || a.battleId.localeCompare(b.battleId))
}

/** The `max` most recently active pairs, kept in first-fought order. */
function mostRecent(lanes: PairLane[], max: number): PairLane[] {
  const keep = new Set(
    [...lanes].sort((a, b) => b.lastLap - a.lastLap || b.firstLap - a.firstLap).slice(0, max).map((l) => l.battleId),
  )
  return lanes.filter((l) => keep.has(l.battleId))
}

export function BattleTimeline({ timeline, currentLap, totalLaps, activeIds, collapsedPairs = 12 }: BattleTimelineProps) {
  const [showAll, setShowAll] = useState(false)
  if (!currentLap) return null

  const laps = Math.max(totalLaps ?? 0, currentLap)
  const x = (lap: number) => ((lap - 1) / laps) * 100
  const allLanes = pairLanes(timeline.episodes)
  const lanes = showAll ? allLanes : mostRecent(allLanes, collapsedPairs)
  const ticks = Array.from({ length: Math.floor(laps / 10) }, (_, i) => (i + 1) * 10).filter((t) => t < laps)
  const runs = lapRuns(timeline.neutralisedLaps)
  const hidden = allLanes.length - lanes.length

  return (
    <section aria-label="Battle timeline" className="rounded-[10px] border border-ink-500 bg-ink-800 p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h2 className="font-display text-xl font-extrabold uppercase tracking-[0.04em]">Battle timeline</h2>
        <div className="flex flex-wrap gap-4 text-[13px] text-mute">
          <span className="flex items-center gap-1.5"><span className="h-2 w-3.5 rounded-sm bg-f1-red" aria-hidden />HOT</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-3.5 rounded-sm bg-watch" aria-hidden />WATCH</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-3.5 rounded-sm bg-sc/30" aria-hidden />Neutralised</span>
          <span className="flex items-center gap-1.5"><span className="h-3 w-0.5 bg-white" aria-hidden />Now</span>
        </div>
      </div>

      {lanes.length === 0 ? (
        <p className="py-6 text-center text-sm text-mute">Battles will appear here lap by lap as they are detected.</p>
      ) : (
        <div className="overflow-x-auto">
          <div className="relative min-w-[640px]">
            {/* Shading and playhead sit behind the lanes, over the bar area only. */}
            <div className="pointer-events-none absolute bottom-6 right-0 top-0" style={{ left: LABEL_W }} aria-hidden>
              {runs.map(([a, b]) => (
                <div key={a} className="absolute inset-y-0 bg-sc/15" style={{ left: `${x(a)}%`, width: `${x(b + 1) - x(a)}%` }} />
              ))}
              {ticks.map((t) => (
                <div key={t} className="absolute inset-y-0 w-px bg-ink-600" style={{ left: `${x(t)}%` }} />
              ))}
              <div className="absolute inset-y-0 w-0.5 bg-white" style={{ left: `${x(currentLap + 0.5)}%` }} />
            </div>

            <ol className="relative">
              {lanes.map((lane) => {
                const active = activeIds.has(lane.battleId)
                const last = lane.segments[lane.segments.length - 1]
                return (
                  <li key={lane.battleId} className="flex h-[30px] items-center">
                    <span className="flex shrink-0 items-center gap-1.5 font-mono text-[13px] text-mute-soft" style={{ width: LABEL_W }}>
                      <span className="h-3 w-[3px] rounded-sm" style={{ backgroundColor: teamColour(lane.chaserColour) }} aria-hidden />
                      {lane.chaser}
                      <span className="text-mute">→</span>
                      <span className="h-3 w-[3px] rounded-sm" style={{ backgroundColor: teamColour(lane.aheadColour) }} aria-hidden />
                      {lane.ahead}
                    </span>
                    <div className="relative h-3 flex-1">
                      {lane.segments.map((e) => {
                        // Only the pair's latest segment can still be running.
                        const ongoing = active && e === last
                        const label = `${e.chaser} chasing ${e.ahead}, ${e.peak}, laps ${e.firstLap} to ${e.lastLap}${ongoing ? ', ongoing' : ''}`
                        return (
                          <div
                            key={e.key}
                            className={`absolute inset-y-0 rounded-[3px] ${e.peak === 'HOT' ? 'bg-f1-red' : 'bg-watch'} ${ongoing ? '' : 'opacity-50'}`}
                            style={{ left: `${x(e.firstLap)}%`, width: `max(6px, ${x(e.lastLap + 1) - x(e.firstLap)}%)` }}
                            role="img"
                            aria-label={label}
                            title={label}
                          />
                        )
                      })}
                    </div>
                  </li>
                )
              })}
            </ol>

            <div className="relative h-6 font-mono text-xs text-mute" style={{ marginLeft: LABEL_W }}>
              <span className="absolute bottom-0 left-0">L1</span>
              {ticks.map((t) => (
                <span key={t} className="absolute bottom-0 -translate-x-1/2" style={{ left: `${x(t)}%` }}>
                  L{t}
                </span>
              ))}
              <span className="absolute bottom-0 right-0">L{laps}</span>
            </div>
          </div>
        </div>
      )}

      <p className="mt-3 text-[13px] text-mute">
        {timeline.startLap && timeline.startLap > 1
          ? `Tracked from lap ${timeline.startLap}, when you tuned in. Faded bars have ended.`
          : 'Faded bars have ended.'}
      </p>
      {(hidden > 0 || showAll) && allLanes.length > collapsedPairs && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="mt-2 min-h-[40px] rounded-md border border-ink-500 px-3 text-[13px] text-mute-strong hover:border-mute hover:text-white"
          aria-expanded={showAll}
        >
          {showAll ? 'Show recent pairs only' : `Show all ${allLanes.length} pairs (${hidden} more)`}
        </button>
      )}
    </section>
  )
}
