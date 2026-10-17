/**
 * Card displaying a single battle between two drivers.
 */
import type { Battle, Tyre } from '../types/api'
import { Sparkline } from './Sparkline'

interface BattleCardProps {
  battle: Battle
  showSparkline?: boolean
  /** Under SC, VSC or a red flag: shown, but visibly not a fight. */
  muted?: boolean
}

const COMPOUNDS: Record<string, { letter: string; className: string }> = {
  SOFT: { letter: 'S', className: 'border-red-500 text-red-400' },
  MEDIUM: { letter: 'M', className: 'border-yellow-400 text-yellow-300' },
  HARD: { letter: 'H', className: 'border-gray-200 text-gray-100' },
  INTERMEDIATE: { letter: 'I', className: 'border-green-500 text-green-400' },
  WET: { letter: 'W', className: 'border-blue-500 text-blue-400' },
}

function TyreBadge({ tyre }: { tyre?: Tyre }) {
  if (!tyre?.compound) return null
  const compound = COMPOUNDS[tyre.compound] ?? { letter: '?', className: 'border-gray-500 text-gray-400' }

  return (
    <span className="inline-flex items-center gap-1 text-xs text-gray-400" title={`${tyre.compound}, ${tyre.age_laps ?? '?'} laps`}>
      <span className={`inline-flex h-5 w-5 items-center justify-center rounded-full border-2 font-bold ${compound.className}`}>
        {compound.letter}
      </span>
      {tyre.age_laps !== null && <span>{tyre.age_laps} laps</span>}
    </span>
  )
}

interface DriverProps {
  position: number
  acronym?: string | null
  name?: string
  team?: string
  colour?: string | null
  tyre?: Tyre
}

function Driver({ position, acronym, name, team, colour, tyre }: DriverProps) {
  return (
    <div className="flex items-center gap-3">
      <span
        className="h-10 w-1.5 rounded-full"
        style={{ backgroundColor: colour ? `#${colour}` : '#4b5563' }}
        aria-hidden
      />
      <div>
        <div className="flex items-baseline gap-2">
          <span className="text-sm text-gray-400">P{position}</span>
          <span className="text-xl font-bold" title={name}>{acronym || name || '?'}</span>
        </div>
        <div className="flex items-center gap-3">
          {team && <span className="text-xs text-gray-500">{team}</span>}
          <TyreBadge tyre={tyre} />
        </div>
      </div>
    </div>
  )
}

export function BattleCard({ battle, showSparkline = true, muted = false }: BattleCardProps) {
  const intensityColor = battle.intensity === 'HOT' ? 'bg-red-600' : 'bg-orange-600'

  return (
    <div
      className={`bg-gray-800 rounded-lg p-4 mb-4 border border-gray-700 transition-opacity ${muted ? 'opacity-40 grayscale' : ''}`}
      aria-disabled={muted}
    >
      <div className="flex flex-wrap items-center justify-between gap-4 mb-3">
        <div className="flex flex-wrap items-center gap-4">
          <Driver
            position={battle.chaser_position}
            acronym={battle.chaser_acronym}
            name={battle.chaser_name}
            team={battle.chaser_team}
            colour={battle.chaser_team_colour}
            tyre={battle.chaser_tyre}
          />
          <span className="text-gray-500" aria-label="chasing">→</span>
          <Driver
            position={battle.ahead_position}
            acronym={battle.ahead_acronym}
            name={battle.ahead_name}
            team={battle.ahead_team}
            colour={battle.ahead_team_colour}
            tyre={battle.ahead_tyre}
          />
        </div>
        <div className="flex items-center gap-3">
          <span className={`${intensityColor} px-2 py-1 rounded text-xs font-semibold`}>
            {battle.intensity}
          </span>
          <span className="text-2xl font-mono">{battle.gap_now_s.toFixed(2)}s</span>
        </div>
      </div>

      <div className="text-sm text-gray-400 mb-2">
        {battle.explanation}
      </div>

      <div className="flex gap-4 text-xs text-gray-500">
        {battle.closing_rate_s_per_s !== null && battle.closing_rate_s_per_s !== undefined && (
          <span>Closing: {battle.closing_rate_s_per_s.toFixed(2)}s/s</span>
        )}
        {battle.pace_delta_s_per_lap !== null && battle.pace_delta_s_per_lap !== undefined && (
          <span>Pace: {battle.pace_delta_s_per_lap.toFixed(2)}s/lap</span>
        )}
      </div>

      {showSparkline && battle.trend_gap_s.length > 0 && (
        <div className="mt-4">
          <Sparkline data={battle.trend_gap_s} />
        </div>
      )}
    </div>
  )
}
