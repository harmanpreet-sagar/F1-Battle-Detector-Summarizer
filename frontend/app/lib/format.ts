/** Small display helpers shared by the battle and timing components. */
import type { Tyre } from '../types/api'

export function teamColour(hex: string | null | undefined): string {
  return hex ? `#${hex}` : '#4b5563'
}

/**
 * A gap in seconds to the thousandth, as F1's own timing screens show it:
 * 0.456, 12.345. One precision everywhere, so a gap reads the same on the
 * timing tower, the battle cards and the score breakdown.
 */
export const GAP_DECIMALS = 3

export function formatGap(seconds: number): string {
  return seconds.toFixed(GAP_DECIMALS)
}

/** "+0.456" for an interval, "Leader" for P1, an en dash when unknown. */
export function formatInterval(position: number, gap: number | null | undefined): string {
  if (position === 1) return 'Leader'
  if (gap === null || gap === undefined) return '–'
  return `+${formatGap(gap)}`
}

/** Gap change per minute, the unit the explanation text already uses. Always positive; see closingLabel. */
export function formatClosing(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return '–'
  return `${(Math.abs(rate) * 60).toFixed(2)}s/min`
}

/** The rate is negative while the chaser closes; once it turns, call it what it is. */
export function closingLabel(rate: number | null | undefined): 'Closing' | 'Opening' {
  return rate !== null && rate !== undefined && rate > 0 ? 'Opening' : 'Closing'
}

/** Pace delta is negative when the chaser is quicker; show it from the chaser's side. */
export function formatPace(delta: number | null | undefined): string {
  if (delta === null || delta === undefined) return '–'
  const quicker = delta < 0
  return `${quicker ? '+' : '−'}${Math.abs(delta).toFixed(2)}s/lap`
}

export function tyreShort(tyre: Tyre | undefined): string | null {
  if (!tyre?.compound) return null
  return `${tyre.compound[0]}${tyre.age_laps ?? ''}`
}

/** How far the gap has moved across the trend window; negative = closing. */
export function trendDelta(trend: number[]): number | null {
  if (trend.length < 2) return null
  return trend[trend.length - 1] - trend[0]
}
