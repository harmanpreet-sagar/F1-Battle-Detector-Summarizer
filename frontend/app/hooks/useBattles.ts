/**
 * SWR hook for fetching top battles with auto-refresh.
 */
import useSWR from 'swr'
import { fetcher } from '../lib/api-client'
import type { Battle, ConnectionStatus } from '../types/api'

interface BattlesResponse {
  battles: Battle[]
  updated_at: string
}

export function useBattles(k: number = 5) {
  const { data, error, isLoading } = useSWR<BattlesResponse>(
    `/battles/top?k=${k}`,
    fetcher,
    {
      refreshInterval: 2000, // Poll every 2 seconds
      dedupingInterval: 1000,
      revalidateOnFocus: false,
    }
  )

  // Calculate data age and connection status
  const dataAge = data?.updated_at
    ? Date.now() - new Date(data.updated_at).getTime()
    : null

  const connectionStatus: ConnectionStatus =
    dataAge === null
      ? 'reconnecting'
      : dataAge < 3000
      ? 'live'
      : dataAge < 10000
      ? 'delayed'
      : 'reconnecting'

  return {
    battles: data?.battles,
    connectionStatus,
    isLoading,
    error,
  }
}
