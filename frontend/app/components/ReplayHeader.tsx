/**
 * The replay header: what race this is, where it is, and that it is a replay.
 *
 *   REPLAY · 2025 Hungarian Grand Prix · Lap 34/70 · 1x
 *
 * The race clock ticks between polls by extrapolating from the last status at
 * the broadcast's speed, so it moves smoothly instead of jumping every 2s.
 */
'use client'

import { useEffect, useRef, useState } from 'react'
import type { ReplayStatus } from '../types/api'

interface ReplayHeaderProps {
  status: ReplayStatus
}

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

export function ReplayHeader({ status }: ReplayHeaderProps) {
  const elapsed = useRaceClock(status)
  const race = [status.year, status.meeting_name].filter(Boolean).join(' ')
  const lap = status.lap && status.total_laps ? `Lap ${status.lap}/${status.total_laps}` : null

  return (
    <header className="mb-8">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm font-semibold uppercase tracking-wide text-gray-300">
        <span className="rounded bg-f1-red px-2 py-0.5 text-white">Replay</span>
        <span>{race}</span>
        {lap && <span className="text-gray-500">·</span>}
        {lap && <span>{lap}</span>}
        {status.speed && <span className="text-gray-500">·</span>}
        {status.speed && <span>{status.speed}x</span>}
      </div>

      <h1 className="mt-3 text-4xl font-bold">F1 Battle Detector</h1>

      <div className="mt-4 flex items-center gap-4">
        <span className="font-mono text-2xl tabular-nums" aria-label="Race time since lights out">
          {elapsed !== null ? formatRaceClock(elapsed) : '-:--:--'}
        </span>
        <div
          className="h-1.5 flex-1 overflow-hidden rounded bg-gray-700"
          role="progressbar"
          aria-valuenow={status.progress_pct ?? 0}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Race progress"
        >
          <div
            className="h-full bg-f1-red transition-[width] duration-1000 ease-linear"
            style={{ width: `${status.progress_pct ?? 0}%` }}
          />
        </div>
      </div>

      <p className="mt-3 inline-block rounded border border-gray-600 px-2 py-1 text-xs text-gray-300">
        Replay of real race data (OpenF1). Not live.
      </p>
    </header>
  )
}
