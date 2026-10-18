/**
 * The header band: what race this is, where it is, and - in replay - that it
 * is a recording.
 *
 *   [REPLAY] 2025 HUNGARIAN GRAND PRIX   Hungaroring    LAP 34/70  0:52:18  5x
 *   ███████████▒▒███████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
 *
 * The race clock ticks between polls by extrapolating from the last status at
 * the broadcast's speed, so it moves smoothly instead of jumping every 2s.
 * Neutralised laps seen since the page opened are drawn on the progress bar.
 */
'use client'

import { useEffect, useRef, useState } from 'react'
import type { ReplayStatus, SessionStatus } from '../types/api'

export function formatRaceClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${h}:${pad(m)}:${pad(s)}`
}

function useRaceClock(status: ReplayStatus): number | null {
  const anchor = useRef<{ elapsed: number; at: number } | null>(null)
  const [elapsed, setElapsed] = useState<number | null>(status.elapsed_s)

  useEffect(() => {
    if (status.elapsed_s === null) return
    anchor.current = { elapsed: status.elapsed_s, at: Date.now() }
    setElapsed(status.elapsed_s)
  }, [status.elapsed_s, status.session_key])

  useEffect(() => {
    const timer = setInterval(() => {
      const a = anchor.current
      if (!a) return
      // A stoppage plays back compressed, so do not extrapolate through it.
      const rate = status.stoppage ? 0 : status.speed ?? 1
      setElapsed(a.elapsed + ((Date.now() - a.at) / 1000) * rate)
    }, 250)
    return () => clearInterval(timer)
  }, [status.speed, status.stoppage])

  return elapsed
}

/** Runs of consecutive laps, e.g. [16,17,18,40] -> [[16,18],[40,40]]. */
export function lapRuns(laps: number[]): [number, number][] {
  const sorted = Array.from(new Set(laps)).sort((a, b) => a - b)
  const runs: [number, number][] = []
  for (const lap of sorted) {
    const last = runs[runs.length - 1]
    if (last && lap === last[1] + 1) last[1] = lap
    else runs.push([lap, lap])
  }
  return runs
}

interface BandProps {
  replay: boolean
  title: string
  subtitle?: string | null
  lap?: number | null
  totalLaps?: number | null
  clock?: string | null
  speed?: number | null
  progressPct?: number | null
  neutralisedLaps?: number[]
}

function HeaderBand({ replay, title, subtitle, lap, totalLaps, clock, speed, progressPct, neutralisedLaps = [] }: BandProps) {
  const runs = totalLaps ? lapRuns(neutralisedLaps) : []
  const lapPct = (l: number) => ((l - 1) / Math.max(1, totalLaps ?? 1)) * 100

  return (
    <header className="border-b border-ink-600 bg-ink-900">
      <div className="mx-auto flex max-w-[1360px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3.5 md:px-6">
        <div className="flex min-w-0 flex-[1_1_360px] flex-wrap items-center gap-x-3 gap-y-1">
          {replay && (
            <span className="rounded-[3px] bg-f1-red px-2.5 py-1 font-display text-sm font-extrabold tracking-[0.08em] text-white">
              REPLAY
            </span>
          )}
          <h1 className="font-display text-[22px] font-bold uppercase tracking-[0.02em]">{title}</h1>
          {subtitle && <span className="text-sm text-mute">{subtitle}</span>}
        </div>

        <div className="flex flex-wrap items-center gap-5">
          {lap ? (
            <div className="flex items-baseline gap-1.5">
              <span className="font-display text-sm font-semibold tracking-[0.08em] text-mute">LAP</span>
              <span className="font-mono text-[26px] font-bold tnum">{lap}</span>
              {totalLaps && <span className="font-mono text-base text-mute tnum">/{totalLaps}</span>}
            </div>
          ) : null}
          {clock && (
            <span className="font-mono text-xl font-medium text-mute-soft tnum" aria-label="Race time since lights out">
              {clock}
            </span>
          )}
          {replay && speed ? (
            <span
              className="rounded-md border border-ink-500 px-3 py-1.5 font-mono text-sm font-bold text-mute-strong"
              title="Everyone watches the same broadcast, so playback speed is set by the server"
            >
              {speed}x
            </span>
          ) : null}
        </div>
      </div>

      {progressPct !== null && progressPct !== undefined && (
        <div className="mx-auto max-w-[1360px] px-4 pb-3.5 md:px-6">
          <div
            className="relative h-2 overflow-hidden rounded bg-ink-600"
            role="progressbar"
            aria-valuenow={Math.round(progressPct)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Race progress"
          >
            <div
              className="absolute inset-y-0 left-0 bg-f1-red transition-[width] duration-1000 ease-linear"
              style={{ width: `${progressPct}%` }}
            />
            {runs.map(([a, b]) => (
              <div
                key={a}
                className="absolute inset-y-0 bg-sc"
                style={{ left: `${lapPct(a)}%`, width: `${lapPct(b + 1) - lapPct(a)}%` }}
                title={a === b ? `Neutralised, lap ${a}` : `Neutralised, laps ${a}–${b}`}
              />
            ))}
          </div>
          <div className="mt-1.5 flex justify-between font-mono text-xs text-mute">
            <span>L1</span>
            {runs.length > 0 && (
              <span className="text-sc">
                SC {runs.map(([a, b]) => (a === b ? `L${a}` : `L${a}–${b}`)).join(', ')}
              </span>
            )}
            <span>{totalLaps ? `L${totalLaps}` : 'Finish'}</span>
          </div>
        </div>
      )}
    </header>
  )
}

export function ReplayHeader({ status, neutralisedLaps }: { status: ReplayStatus; neutralisedLaps?: number[] }) {
  const elapsed = useRaceClock(status)
  const race = [status.year, status.meeting_name].filter(Boolean).join(' ')

  return (
    <HeaderBand
      replay
      title={race || 'Replay'}
      subtitle={[status.circuit, 'Recorded race, not live'].filter(Boolean).join(' · ')}
      lap={status.lap}
      totalLaps={status.total_laps}
      clock={elapsed !== null ? formatRaceClock(elapsed) : null}
      speed={status.speed}
      progressPct={status.progress_pct ?? 0}
      neutralisedLaps={neutralisedLaps}
    />
  )
}

export function LiveHeader({ session }: { session: SessionStatus | undefined }) {
  const lap = session?.current_lap
  const total = session?.total_laps
  return (
    <HeaderBand
      replay={false}
      title={session?.meeting_name ?? 'F1 Battle Detector'}
      subtitle={session?.session_name}
      lap={lap}
      totalLaps={total}
      progressPct={lap && total ? (lap / total) * 100 : null}
    />
  )
}
