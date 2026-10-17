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
    <div className="mt-8 rounded-lg border border-gray-700 px-4 py-3 text-sm text-gray-400">
      <span className="mr-2 font-semibold uppercase tracking-wide text-gray-300">Up next</span>
      {repeat ? 'This race again, from lights out' : `${race.year ?? ''} ${race.meeting_name}`.trim()}
    </div>
  )
}
