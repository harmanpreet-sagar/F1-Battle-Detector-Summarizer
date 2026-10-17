/**
 * The timing tower: all twenty cars in order, with the cars in a detected
 * battle marked. The chaser's row carries the HOT/WATCH tag; both rows get the
 * edge so the pair reads as one bracket.
 */
import type { Battle, BattleIntensity, DriverState } from '../types/api'
import { formatInterval, teamColour } from '../lib/format'
import { IntensityTag } from './IntensityTag'
import { TyreBadge } from './TyreBadge'

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

export function RunningOrder({ drivers, battles, isLoading = false, muted = false }: RunningOrderProps) {
  const marks = muted ? new Map<number, Mark>() : marksFor(battles)
  const sorted = [...drivers].sort((a, b) => a.position - b.position)

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
            const edge = mark ? (mark.intensity === 'HOT' ? '#E10600' : '#F59E0B') : 'transparent'
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
                {mark?.chaser && <IntensityTag intensity={mark.intensity} size="sm" />}
                <span className="w-14 text-right font-mono text-[13px] text-mute-soft tnum">
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
