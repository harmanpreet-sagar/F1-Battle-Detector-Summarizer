'use client'

import { ConnectionStatus } from './components/ConnectionStatus'
import { BattlesList } from './components/BattlesList'
import { useBattles } from './hooks/useBattles'
import { useSession } from './hooks/useSession'

export default function Home() {
  const { battles, connectionStatus, isLoading } = useBattles(5)
  const { session } = useSession()

  return (
    <main className="min-h-screen p-4 md:p-8">
      <ConnectionStatus status={connectionStatus} />
      
      <div className="max-w-7xl mx-auto mt-8">
        <header className="mb-8">
          <h1 className="text-4xl font-bold mb-2">F1 Battle Detector</h1>
          {session && (
            <p className="text-gray-400">
              {session.meeting_name} • {session.session_name}
              {session.current_lap && ` • Lap ${session.current_lap}`}
            </p>
          )}
        </header>

        <div>
          <h2 className="text-2xl font-semibold mb-4">Top Battles Now</h2>
          {isLoading ? (
            <div className="text-gray-400">Loading battles...</div>
          ) : (
            <BattlesList battles={battles || []} />
          )}
        </div>
      </div>
    </main>
  )
}
