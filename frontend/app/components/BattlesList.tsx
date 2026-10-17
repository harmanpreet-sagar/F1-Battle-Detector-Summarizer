/**
 * The top battle as a featured card, the rest as a grid of compact cards.
 */
import type { Battle } from '../types/api'
import { BattleCard } from './BattleCard'

interface BattlesListProps {
  battles: Battle[]
  muted?: boolean
  /** How many compact cards to show under the featured one. */
  maxCompact?: number
}

export function BattlesList({ battles, muted = false, maxCompact = 4 }: BattlesListProps) {
  if (battles.length === 0) {
    return (
      <div className="rounded-[10px] border border-dashed border-ink-500 py-12 text-center text-mute">
        {muted ? 'No battles while the field is neutralised' : 'No active battles at the moment'}
      </div>
    )
  }

  const [top, ...rest] = battles

  return (
    <div className="flex flex-col gap-5">
      <BattleCard battle={top} variant="featured" muted={muted} />
      {rest.length > 0 && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-5">
          {rest.slice(0, maxCompact).map((battle) => (
            <BattleCard key={battle.battle_id} battle={battle} muted={muted} />
          ))}
        </div>
      )}
    </div>
  )
}
