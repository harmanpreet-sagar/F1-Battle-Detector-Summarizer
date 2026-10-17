/**
 * A battle between two drivers. The top battle gets the featured card - big
 * gap, wide trend, stat tiles and the score's working - and the rest get a
 * compact card.
 */
import type { Battle } from '../types/api'
import { closingLabel, formatClosing, formatGap, formatPace, teamColour, trendDelta, tyreShort } from '../lib/format'
import { IntensityTag } from './IntensityTag'
import { ScoreBreakdown } from './ScoreBreakdown'
import { Sparkline } from './Sparkline'
import { TyreFromBattle } from './TyreBadge'

interface BattleCardProps {
  battle: Battle
  variant?: 'featured' | 'compact'
  /** Under SC, VSC or a red flag: shown, but visibly not a fight. */
  muted?: boolean
}

function Side({ battle, side, large }: { battle: Battle; side: 'chaser' | 'ahead'; large: boolean }) {
  const position = side === 'chaser' ? battle.chaser_position : battle.ahead_position
  const acronym = side === 'chaser' ? battle.chaser_acronym : battle.ahead_acronym
  const name = side === 'chaser' ? battle.chaser_name : battle.ahead_name
  const team = side === 'chaser' ? battle.chaser_team : battle.ahead_team
  const colour = side === 'chaser' ? battle.chaser_team_colour : battle.ahead_team_colour
  const tyre = side === 'chaser' ? battle.chaser_tyre : battle.ahead_tyre

  return (
    <div className="flex items-center gap-2.5">
      <span
        className={`${large ? 'h-12 w-1.5' : 'h-7 w-1'} shrink-0 rounded-full`}
        style={{ backgroundColor: teamColour(colour) }}
        aria-hidden
      />
      <div className="min-w-0">
        {large && (
          <div className="truncate font-mono text-[13px] text-mute">
            P{position}
            {team && ` · ${team}`}
          </div>
        )}
        <div className="flex items-center gap-2">
          <span
            className={`font-display font-extrabold leading-none ${large ? 'text-[40px]' : 'text-[26px]'}`}
            title={name}
          >
            {acronym || name || '?'}
          </span>
          {large && <TyreFromBattle tyre={tyre} showAge />}
        </div>
      </div>
    </div>
  )
}

function Pair({ battle, large }: { battle: Battle; large: boolean }) {
  return (
    <div className={`flex flex-wrap items-center ${large ? 'gap-x-5 gap-y-2' : 'gap-x-2.5 gap-y-1'}`}>
      <Side battle={battle} side="chaser" large={large} />
      <svg width={large ? 28 : 18} height="16" viewBox="0 0 28 16" role="img" aria-label="chasing" className="shrink-0">
        <path d="M2 8h22M18 2l6 6-6 6" fill="none" stroke="#8A8A99" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <Side battle={battle} side="ahead" large={large} />
    </div>
  )
}

function Delta({ battle }: { battle: Battle }) {
  const delta = trendDelta(battle.trend_gap_s)
  if (delta === null) return null
  const closing = delta < 0
  return (
    <div className={`font-mono text-[13px] tnum ${closing ? 'text-closing' : 'text-opening'}`}>
      {closing ? '−' : '+'}
      {formatGap(Math.abs(delta))}s over {battle.trend_gap_s.length} readings
    </div>
  )
}

function Flags({ battle }: { battle: Battle }) {
  const notes = [
    battle.flags.pit_window_active && 'Pit window',
    battle.flags.data_quality_warning && 'Stale timing data',
  ].filter(Boolean) as string[]
  if (notes.length === 0) return null
  return (
    <div className="flex flex-wrap gap-2">
      {notes.map((n) => (
        <span key={n} className="rounded border border-ink-500 px-2 py-0.5 text-xs text-mute-strong">
          {n}
        </span>
      ))}
    </div>
  )
}

function FeaturedCard({ battle }: { battle: Battle }) {
  const chaserTyre = tyreShort(battle.chaser_tyre)
  const aheadTyre = tyreShort(battle.ahead_tyre)
  const stats = [
    { label: closingLabel(battle.closing_rate_s_per_s), value: formatClosing(battle.closing_rate_s_per_s) },
    { label: 'Pace Δ', value: formatPace(battle.pace_delta_s_per_lap) },
    { label: 'Tyres', value: chaserTyre && aheadTyre ? `${chaserTyre} vs ${aheadTyre}` : '–' },
    { label: 'Score', value: battle.battle_score.toFixed(2) },
  ]

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <Pair battle={battle} large />
        <div className="flex items-center gap-4">
          <IntensityTag intensity={battle.intensity} size="lg" />
          <div className="text-right">
            <div className="font-mono text-[56px] font-bold leading-none tnum">
              {formatGap(battle.gap_now_s)}
              <span className="text-2xl text-mute">s</span>
            </div>
            <Delta battle={battle} />
          </div>
        </div>
      </div>

      {battle.trend_gap_s.length > 1 && <Sparkline data={battle.trend_gap_s} height={120} showOneSecond />}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {stats.map((s) => (
          <div key={s.label} className="rounded-md border border-ink-600 bg-ink-950/60 p-3">
            <div className="font-display text-[13px] font-semibold uppercase tracking-[0.08em] text-mute">{s.label}</div>
            <div className="mt-1 font-mono text-xl font-bold tnum">{s.value}</div>
          </div>
        ))}
      </div>

      <p className="text-base leading-relaxed text-mute-soft">{battle.explanation}</p>
      <Flags battle={battle} />

      {battle.score_breakdown && <ScoreBreakdown battle={battle} breakdown={battle.score_breakdown} />}
    </>
  )
}

function CompactCard({ battle }: { battle: Battle }) {
  const chaserTyre = tyreShort(battle.chaser_tyre)
  const aheadTyre = tyreShort(battle.ahead_tyre)
  const line = [
    `${closingLabel(battle.closing_rate_s_per_s)} ${formatClosing(battle.closing_rate_s_per_s)}`,
    formatPace(battle.pace_delta_s_per_lap),
    chaserTyre && aheadTyre ? `${chaserTyre} vs ${aheadTyre}` : null,
  ].filter(Boolean)

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <Pair battle={battle} large={false} />
        <IntensityTag intensity={battle.intensity} />
      </div>
      <div className="flex items-end justify-between gap-3">
        <div className="font-mono text-[34px] font-bold leading-none tnum">
          {formatGap(battle.gap_now_s)}
          <span className="text-base text-mute">s</span>
        </div>
        <div className="w-40">
          <Sparkline data={battle.trend_gap_s} height={40} />
        </div>
      </div>
      <div className="font-mono text-[13px] text-mute-strong tnum">
        P{battle.chaser_position} vs P{battle.ahead_position} · {line.join(' · ')}
      </div>
      <Flags battle={battle} />
    </>
  )
}

export function BattleCard({ battle, variant = 'compact', muted = false }: BattleCardProps) {
  const featured = variant === 'featured'
  return (
    <article
      className={`flex flex-col rounded-[10px] border border-ink-500 bg-ink-800 transition-opacity ${
        featured ? 'gap-5 p-6' : 'gap-3 p-[18px]'
      } ${muted ? 'opacity-40 grayscale' : ''}`}
    >
      {featured ? <FeaturedCard battle={battle} /> : <CompactCard battle={battle} />}
    </article>
  )
}
