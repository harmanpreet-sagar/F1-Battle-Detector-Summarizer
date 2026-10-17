/**
 * Why a battle scored what it did: the three weighted terms as a stacked bar on
 * the 0-1 score scale, with the WATCH and HOT thresholds marked, then each term
 * beside the input it came from.
 */
import type { Battle, ScoreBreakdown as Breakdown } from '../types/api'
import { HOT_SCORE, WATCH_SCORE } from '../lib/scoring'
import { closingLabel, formatClosing, formatPace } from '../lib/format'

const TERMS = [
  { key: 'gap', label: 'Gap', weight: 0.5, swatch: 'bg-f1-red' },
  { key: 'closing', label: 'Closing', weight: 0.3, swatch: 'bg-[#FF8A82]' },
  { key: 'pace', label: 'Pace', weight: 0.2, swatch: 'bg-mute-soft' },
] as const

function penaltyReasons(battle: Battle): string[] {
  const reasons: string[] = []
  if (battle.flags.under_yellow) reasons.push('yellow flag ×0.5')
  if (battle.flags.pit_window_active) reasons.push('pit window ×0.3')
  if (battle.flags.data_quality_warning) reasons.push('stale data ×0.7')
  return reasons
}

export function ScoreBreakdown({ battle, breakdown }: { battle: Battle; breakdown: Breakdown }) {
  const inputs: Record<(typeof TERMS)[number]['key'], string> = {
    gap: `${battle.gap_now_s.toFixed(2)}s`,
    closing:
      closingLabel(battle.closing_rate_s_per_s) === 'Opening'
        ? `opening ${formatClosing(battle.closing_rate_s_per_s)}`
        : formatClosing(battle.closing_rate_s_per_s),
    pace: formatPace(battle.pace_delta_s_per_lap),
  }
  const penalties = penaltyReasons(battle)
  const pct = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`

  return (
    <section aria-label="Score breakdown" className="rounded-md border border-ink-600 bg-ink-950/60 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-mute-strong">
          Why it scores {breakdown.score.toFixed(2)}
        </h3>
        <span className="font-mono text-xs text-mute">
          WATCH {WATCH_SCORE.toFixed(2)} · HOT {HOT_SCORE.toFixed(2)}
        </span>
      </div>

      <div className="relative mt-3 h-3 rounded-full bg-ink-600" role="img" aria-label={`Score ${breakdown.score.toFixed(2)} of 1`}>
        <div className="absolute inset-y-0 left-0 flex overflow-hidden rounded-full" style={{ width: pct(breakdown.raw) }}>
          {TERMS.map((t) => (
            <div key={t.key} className={t.swatch} style={{ width: breakdown.raw > 0 ? `${(breakdown[t.key] / breakdown.raw) * 100}%` : 0 }} />
          ))}
        </div>
        {breakdown.multiplier < 1 && (
          <div
            className="absolute -inset-y-1 w-0.5 bg-white"
            style={{ left: pct(breakdown.score) }}
            title="Score after penalties"
          />
        )}
        <div className="absolute -inset-y-1.5 w-px bg-watch" style={{ left: pct(WATCH_SCORE) }} aria-hidden />
        <div className="absolute -inset-y-1.5 w-px bg-white" style={{ left: pct(HOT_SCORE) }} aria-hidden />
      </div>

      <dl className="mt-4 grid grid-cols-[auto_1fr_auto] gap-x-4 gap-y-1.5 font-mono text-sm tnum">
        {TERMS.map((t) => (
          <div key={t.key} className="contents">
            <dt className="flex items-center gap-2 text-mute">
              <span className={`h-2.5 w-2.5 rounded-sm ${t.swatch}`} aria-hidden />
              {t.label} ×{t.weight}
            </dt>
            <dd className="text-right text-mute-soft">{inputs[t.key]}</dd>
            <dd className="text-right">{breakdown[t.key].toFixed(2)}</dd>
          </div>
        ))}
        {penalties.length > 0 && (
          <div className="contents">
            <dt className="text-mute">Penalty</dt>
            <dd className="text-right text-mute-soft">{penalties.join(', ')}</dd>
            <dd className="text-right">×{breakdown.multiplier.toFixed(2)}</dd>
          </div>
        )}
        <div className="contents">
          <dt className="border-t border-ink-500 pt-1.5 text-white">Score</dt>
          <dd className="border-t border-ink-500 pt-1.5" />
          <dd className="border-t border-ink-500 pt-1.5 text-right font-bold">{breakdown.score.toFixed(2)}</dd>
        </div>
      </dl>
    </section>
  )
}
