'use client'

import { ConnectionStatus } from './components/ConnectionStatus'
import { BattlesList } from './components/BattlesList'
import { Footer } from './components/Footer'
import { ReplayHeader } from './components/ReplayHeader'
import { TrackStatusBanner, isNeutralised } from './components/TrackStatusBanner'
import { UpNext } from './components/UpNext'
import { useBattles } from './hooks/useBattles'
import { useReplayStatus } from './hooks/useReplayStatus'
import { useSession } from './hooks/useSession'

export default function Home() {
  const { battles, connectionStatus, isLoading, error: battlesError } = useBattles(5)
  const { session } = useSession()
  const { status, isReplay } = useReplayStatus()

  // Freshness against the wall clock means nothing for a 2025 race, and the
  // banner must never say LIVE over a replay.
  const shownStatus = isReplay ? (battlesError ? 'reconnecting' : 'replay') : connectionStatus
  const trackStatus = isReplay ? status?.track_status : session?.track_status
  const neutralised = isNeutralised(trackStatus)

  return (
    <main className="min-h-screen p-4 md:p-8">
      <ConnectionStatus status={shownStatus} />

      <div className="max-w-7xl mx-auto mt-8">
        {isReplay && status ? (
          <ReplayHeader status={status} />
        ) : (
          <header className="mb-8">
            <h1 className="text-4xl font-bold mb-2">F1 Battle Detector</h1>
            {session && (
              <p className="text-gray-400">
                {session.meeting_name} • {session.session_name}
                {session.current_lap && ` • Lap ${session.current_lap}`}
                {session.current_lap && session.total_laps && `/${session.total_laps}`}
              </p>
            )}
          </header>
        )}

        <TrackStatusBanner status={trackStatus} stoppage={status?.stoppage} />

        <div>
          <h2 className="text-2xl font-semibold mb-4">Top Battles Now</h2>
          {isLoading ? (
            <div className="text-gray-400">Loading battles...</div>
          ) : (
            <BattlesList battles={battles || []} muted={neutralised} />
          )}
        </div>

        {isReplay && status && (
          <UpNext race={status.next_race} currentSessionKey={status.session_key} />
        )}
      </div>

      <Footer />
    </main>
  )
}
