/** A tyre compound as the broadcast draws it: a coloured ring and a letter. */
import type { Tyre } from '../types/api'

const COMPOUNDS: Record<string, { letter: string; className: string; name: string }> = {
  SOFT: { letter: 'S', className: 'border-red-500 text-red-400', name: 'Soft' },
  MEDIUM: { letter: 'M', className: 'border-yellow-400 text-yellow-300', name: 'Medium' },
  HARD: { letter: 'H', className: 'border-gray-200 text-gray-100', name: 'Hard' },
  INTERMEDIATE: { letter: 'I', className: 'border-green-500 text-green-400', name: 'Intermediate' },
  WET: { letter: 'W', className: 'border-blue-500 text-blue-400', name: 'Wet' },
}

interface TyreBadgeProps {
  compound: string | null | undefined
  ageLaps?: number | null
  /** Print the tyre's age beside the ring. */
  showAge?: boolean
  size?: 'sm' | 'md'
}

export function TyreBadge({ compound, ageLaps, showAge = false, size = 'md' }: TyreBadgeProps) {
  if (!compound) return <span className={size === 'sm' ? 'w-5' : 'w-6'} aria-hidden />
  const c = COMPOUNDS[compound] ?? { letter: '?', className: 'border-gray-500 text-gray-400', name: compound }
  const ring = size === 'sm' ? 'h-5 w-5 text-[11px]' : 'h-6 w-6 text-xs'
  const label = ageLaps !== null && ageLaps !== undefined ? `${c.name}, ${ageLaps} laps old` : c.name

  return (
    <span className="inline-flex items-center gap-1.5" title={label}>
      <span
        className={`inline-flex ${ring} items-center justify-center rounded-full border-2 font-mono font-bold ${c.className}`}
        role="img"
        aria-label={label}
      >
        {c.letter}
      </span>
      {showAge && ageLaps !== null && ageLaps !== undefined && (
        <span className="font-mono text-xs text-mute tnum">{ageLaps}L</span>
      )}
    </span>
  )
}

export function TyreFromBattle({ tyre, showAge }: { tyre?: Tyre; showAge?: boolean }) {
  return <TyreBadge compound={tyre?.compound} ageLaps={tyre?.age_laps} showAge={showAge} />
}
