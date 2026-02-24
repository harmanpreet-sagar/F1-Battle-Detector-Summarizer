/**
 * Card displaying a single battle between two drivers.
 */
import type { Battle } from '../types/api'
import { Sparkline } from './Sparkline'

interface BattleCardProps {
  battle: Battle
  showSparkline?: boolean
}

export function BattleCard({ battle, showSparkline = true }: BattleCardProps) {
  const intensityColor = battle.intensity === 'HOT' ? 'bg-red-600' : 'bg-orange-600'

  return (
    <div className="bg-gray-800 rounded-lg p-4 mb-4 border border-gray-700">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-4">
          <span className="text-2xl font-bold">
            P{battle.chaser_position} → P{battle.ahead_position}
          </span>
          <span className={`${intensityColor} px-2 py-1 rounded text-xs font-semibold`}>
            {battle.intensity}
          </span>
        </div>
        <div className="text-right">
          <div className="text-2xl font-mono">{battle.gap_now_s.toFixed(2)}s</div>
        </div>
      </div>

      <div className="text-sm text-gray-400 mb-2">
        {battle.explanation}
      </div>

      <div className="flex gap-4 text-xs text-gray-500">
        {battle.closing_rate_s_per_s !== null && (
          <span>Closing: {battle.closing_rate_s_per_s.toFixed(2)}s/s</span>
        )}
        {battle.pace_delta_s_per_lap !== null && (
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
