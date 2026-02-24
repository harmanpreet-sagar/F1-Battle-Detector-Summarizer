/**
 * List of battle cards.
 */
import type { Battle } from '../types/api'
import { BattleCard } from './BattleCard'

interface BattlesListProps {
  battles: Battle[]
}

export function BattlesList({ battles }: BattlesListProps) {
  if (battles.length === 0) {
    return (
      <div className="text-gray-400 text-center py-8">
        No active battles at the moment
      </div>
    )
  }

  return (
    <div>
      {battles.map((battle) => (
        <BattleCard key={battle.battle_id} battle={battle} />
      ))}
    </div>
  )
}
