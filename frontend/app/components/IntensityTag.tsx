/** HOT or WATCH. HOT is red on white text, WATCH amber on dark: they differ in lightness, not hue alone. */
import type { BattleIntensity } from '../types/api'

export function IntensityTag({ intensity, size = 'md' }: { intensity: BattleIntensity; size?: 'sm' | 'md' | 'lg' }) {
  if (intensity === 'NONE') return null
  const colour = intensity === 'HOT' ? 'bg-f1-red text-white' : 'bg-watch text-ink-950'
  const sizing = {
    sm: 'px-1.5 py-0.5 text-[11px]',
    md: 'px-2 py-1 text-[13px]',
    lg: 'px-3 py-1.5 text-base',
  }[size]

  return (
    <span className={`${colour} ${sizing} rounded-[3px] font-display font-extrabold uppercase tracking-[0.1em]`}>
      {intensity}
    </span>
  )
}
