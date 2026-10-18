/**
 * Connection status strip for live data. In replay the header carries the
 * REPLAY label instead, and this only appears if the connection drops.
 */
import type { ConnectionStatus as Status } from '../types/api'

const CONFIG: Record<Status, { className: string; dot: string; text: string }> = {
  live: { className: 'bg-green-700', dot: 'bg-green-300', text: 'Live' },
  delayed: { className: 'bg-yellow-600 text-ink-950', dot: 'bg-ink-950', text: 'Delayed' },
  reconnecting: { className: 'bg-red-700', dot: 'bg-red-200', text: 'Reconnecting' },
  // Never imply a replay is live.
  replay: { className: 'bg-ink-600', dot: 'bg-mute', text: 'Replay · Recorded race' },
}

export function ConnectionStatus({ status }: { status: Status }) {
  const c = CONFIG[status]
  return (
    <div role="status" className={`${c.className} flex items-center justify-center gap-2 px-4 py-1.5 font-display text-sm font-bold uppercase tracking-[0.1em]`}>
      <span className={`h-2 w-2 rounded-full ${c.dot} ${status === 'live' ? 'animate-pulse' : ''}`} aria-hidden />
      {c.text}
    </div>
  )
}
