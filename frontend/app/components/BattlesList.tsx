/**
 * List of battle cards.
 */
import type { Battle } from '../types/api'
import { BattleCard } from './BattleCard'

interface BattlesListProps {
  battles: Battle[]
  muted?: boolean
}

export function BattlesList({ battles, muted = false }: BattlesListProps) {
  if (battles.length === 0) {
    return (
      <div className="text-gray-400 text-center py-8">
        {muted ? 'No battles while the field is neutralised' : 'No active battles at the moment'}
      </div>
    )
  }

  return (
    <div>
      {battles.map((battle) => (
        <BattleCard key={battle.battle_id} battle={battle} muted={muted} />
      ))}
    </div>
  )
}
