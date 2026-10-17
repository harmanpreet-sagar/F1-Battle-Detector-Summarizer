'use client'

import { useMemo } from 'react'
import { BattleTimeline } from './components/BattleTimeline'
import { BattlesList } from './components/BattlesList'
import { ConnectionStatus } from './components/ConnectionStatus'
import { Footer } from './components/Footer'
import { LiveHeader, ReplayHeader } from './components/ReplayHeader'
import { RunningOrder } from './components/RunningOrder'
import { TrackStatusBanner, isNeutralised } from './components/TrackStatusBanner'
import { UpNext } from './components/UpNext'
import { useBattleTimeline } from './hooks/useBattleTimeline'
import { useBattles } from './hooks/useBattles'
import { useReplayStatus } from './hooks/useReplayStatus'
import { useRunningOrder } from './hooks/useRunningOrder'
import { useSession } from './hooks/useSession'

export default function Home() {
  // More than the cards show: the timeline wants every pair, not just the top few.
  const { battles, connectionStatus, isLoading, error: battlesError } = useBattles(8)
  const { drivers, isLoading: orderLoading } = useRunningOrder()
  const { session } = useSession()
  const { status, isReplay } = useReplayStatus()

  // Freshness against the wall clock means nothing for a 2025 race, and the
  // banner must never say LIVE over a replay.
  const shownStatus = isReplay ? (battlesError ? 'reconnecting' : 'replay') : connectionStatus
  const trackStatus = isReplay ? status?.track_status : session?.track_status
  const neutralised = isNeutralised(trackStatus)
  const lap = isReplay ? status?.lap : session?.current_lap
  const totalLaps = isReplay ? status?.total_laps : session?.total_laps

  const timeline = useBattleTimeline({
    sessionKey: isReplay ? status?.session_key : session?.session_key,
    lap,
    battles,
    trackStatus,
  })
  const activeIds = useMemo(() => new Set((battles ?? []).map((b) => b.battle_id)), [battles])
  const count = battles?.length ?? 0

  return (
    <div className="min-h-screen">
      {/* In replay the header says REPLAY; the strip only returns if the feed drops. */}
      {shownStatus !== 'replay' && <ConnectionStatus status={shownStatus} />}

      {isReplay && status ? (
        <ReplayHeader status={status} neutralisedLaps={timeline.neutralisedLaps} />
      ) : (
        <LiveHeader session={session} />
      )}

      <main className="mx-auto flex max-w-[1360px] flex-wrap items-start gap-6 px-4 py-6 md:px-6">
        {/* On a phone the battles come first; the tower follows. */}
        <div className="order-2 w-full flex-[1_1_300px] md:order-1 md:max-w-[340px]">
          <RunningOrder drivers={drivers} battles={battles ?? []} isLoading={orderLoading} muted={neutralised} />
        </div>

        <div className="order-1 flex min-w-0 flex-[999_1_560px] flex-col gap-5 md:order-2">
          <TrackStatusBanner status={trackStatus} stoppage={status?.stoppage} />

          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-display text-[34px] font-extrabold uppercase leading-none tracking-[0.02em]">
              Top battles now
            </h2>
            {!isLoading && (
              <span className="text-sm text-mute">
                {count} active{count > 0 && ' · ranked by score and points at stake'}
              </span>
            )}
          </div>

          {isLoading ? (
            <div className="text-mute">Loading battles…</div>
          ) : (
            <BattlesList battles={battles || []} muted={neutralised} />
          )}

          <BattleTimeline timeline={timeline} currentLap={lap} totalLaps={totalLaps} activeIds={activeIds} />

          {isReplay && status && <UpNext race={status.next_race} currentSessionKey={status.session_key} />}
        </div>
      </main>

      <div className="px-4 pb-8 md:px-6">
        <Footer />
      </div>
    </div>
  )
}
