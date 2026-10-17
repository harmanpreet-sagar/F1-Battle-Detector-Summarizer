/** The next race in the replay playlist. */
import type { ReplayRace } from '../types/api'

interface UpNextProps {
  race: ReplayRace | null
  currentSessionKey: number | null
}

export function UpNext({ race, currentSessionKey }: UpNextProps) {
  if (!race) return null
  const repeat = race.session_key === currentSessionKey

  return (
    <div className="rounded-[10px] border border-ink-600 px-4 py-3 text-sm text-mute-soft">
      <span className="mr-2 font-display font-bold uppercase tracking-[0.08em] text-mute-strong">Up next</span>
      {repeat ? 'This race again, from lights out' : `${race.year ?? ''} ${race.meeting_name}`.trim()}
    </div>
  )
}
