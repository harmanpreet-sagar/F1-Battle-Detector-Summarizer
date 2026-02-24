/**
 * SWR hook for fetching current session info.
 */
import useSWR from 'swr'
import { fetcher } from '../lib/api-client'
import type { SessionStatus } from '../types/api'

export function useSession() {
  const { data, error, isLoading } = useSWR<SessionStatus>(
    '/session/current',
    fetcher,
    {
      refreshInterval: 10000, // Poll every 10 seconds
      revalidateOnFocus: false,
    }
  )

  return {
    session: data,
    isLoading,
    error,
  }
}
