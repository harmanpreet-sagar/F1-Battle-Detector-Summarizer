/**
 * The timing tower: all twenty cars in order, with the cars in a detected
 * battle marked. The chaser's row carries the HOT/WATCH tag; both rows get the
 * edge so the pair reads as one bracket.
 *
 * Pairs inside a second of each other that are not a battle - the chaser is
 * neither closing nor quicker, so the score never reaches WATCH - get a quieter
 * mark of their own: a grey edge and an outlined CLOSE tag. Without it the
 * tower shows two cars half a second apart with nothing said about them, which
 * reads as a missed battle. This is display only, worked out from the gaps the
 * tower already has; it is not detected or stability-filtered, so it can blink
 * as a gap crosses the threshold.
 */
import type { Battle, BattleIntensity, DriverState } from '../types/api'
import { formatInterval, teamColour } from '../lib/format'
import { IntensityTag } from './IntensityTag'
import { TyreBadge } from './TyreBadge'

/** Gap to the car ahead under which a pair is worth noting even when it isn't a battle. */
export const CLOSE_GAP_S = 1.0

const EDGE = { HOT: '#E10600', WATCH: '#F59E0B', CLOSE: '#6E6E80' } as const

interface RunningOrderProps {
  drivers: DriverState[]
  battles: Battle[]
  isLoading?: boolean
  muted?: boolean
}

interface Mark {
  intensity: BattleIntensity
  chaser: boolean
}

function marksFor(battles: Battle[]): Map<number, Mark> {
  const marks = new Map<number, Mark>()
  for (const b of battles) {
    // A car can be the chaser of one pair and the target of another; the
    // chaser role carries the tag, so it wins.
    marks.set(b.chaser_driver_number, { intensity: b.intensity, chaser: true })
    if (!marks.get(b.ahead_driver_number)?.chaser) {
      marks.set(b.ahead_driver_number, { intensity: b.intensity, chaser: false })
    }
  }
  return marks
}

/**
 * Adjacent pairs under CLOSE_GAP_S that are not already a battle, as the
 * chasers' and the targets' driver numbers.
 */
export function closePairs(sorted: DriverState[], battles: Battle[]): { chasers: Set<number>; targets: Set<number> } {
  const battleIds = new Set(battles.map((b) => `${b.chaser_driver_number}_${b.ahead_driver_number}`))
  const chasers = new Set<number>()
  const targets = new Set<number>()
  for (let i = 1; i < sorted.length; i++) {
    const ahead = sorted[i - 1]
    const chaser = sorted[i]
    const gap = chaser.gap_to_ahead_s
    if (gap === null || gap === undefined || gap <= 0 || gap >= CLOSE_GAP_S) continue
    if (battleIds.has(`${chaser.driver_number}_${ahead.driver_number}`)) continue
    chasers.add(chaser.driver_number)
    targets.add(ahead.driver_number)
  }
  return { chasers, targets }
}

function CloseTag() {
  return (
    <span
      className="rounded-[3px] border border-mute/60 px-1.5 py-0.5 font-display text-[11px] font-bold uppercase tracking-[0.1em] text-mute-strong"
      title={`Within ${CLOSE_GAP_S.toFixed(1)}s of the car ahead, but not closing or quicker, so not scored as a battle`}
    >
      Close
    </span>
  )
}

export function RunningOrder({ drivers, battles, isLoading = false, muted = false }: RunningOrderProps) {
  const sorted = [...drivers].sort((a, b) => a.position - b.position)
  // Under SC/VSC/red the whole field bunches up, so nothing is marked.
  const marks = muted ? new Map<number, Mark>() : marksFor(battles)
  const close = muted ? { chasers: new Set<number>(), targets: new Set<number>() } : closePairs(sorted, battles)

  return (
    <section aria-label="Running order" className="overflow-hidden rounded-lg border border-ink-600 bg-ink-850">
      <div className="flex justify-between border-b border-ink-600 px-3.5 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-mute-strong">
        <span>Running order</span>
        <span>Int</span>
      </div>

      {sorted.length === 0 ? (
        <div className="px-3.5 py-8 text-center text-sm text-mute">
          {isLoading ? 'Loading timing…' : 'Waiting for timing data'}
        </div>
      ) : (
        <ol>
          {sorted.map((d) => {
            const mark = marks.get(d.driver_number)
            const isClose = close.chasers.has(d.driver_number) || close.targets.has(d.driver_number)
            // A battle's edge wins over a close pair's on a car that is in both.
            const edge = mark ? EDGE[mark.intensity === 'HOT' ? 'HOT' : 'WATCH'] : isClose ? EDGE.CLOSE : 'transparent'
            return (
              <li
                key={d.driver_number}
                className={`flex items-center gap-2.5 border-b border-ink-700 px-3.5 py-[7px] last:border-b-0 ${mark ? 'bg-ink-750' : ''}`}
                style={{ boxShadow: `inset 3px 0 0 ${edge}` }}
              >
                <span className="w-[22px] text-right font-mono text-[13px] text-mute tnum">{d.position}</span>
                <span className="h-[18px] w-1 shrink-0 rounded-sm" style={{ backgroundColor: teamColour(d.team_colour) }} aria-hidden />
                <span className="w-11 font-display text-lg font-bold tracking-[0.04em]" title={d.full_name}>
                  {d.name_acronym || d.driver_number}
                </span>
                <TyreBadge compound={d.tire_compound} ageLaps={d.tire_age_laps} size="sm" />
                <span className="flex-1" />
                {mark?.chaser ? (
                  <IntensityTag intensity={mark.intensity} size="sm" />
                ) : (
                  close.chasers.has(d.driver_number) && <CloseTag />
                )}
                <span className="w-[62px] text-right font-mono text-[13px] text-mute-soft tnum">
                  {formatInterval(d.position, d.gap_to_ahead_s)}
                </span>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
