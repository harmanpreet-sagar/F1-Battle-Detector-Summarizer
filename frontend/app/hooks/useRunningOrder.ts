/**
 * SWR hook for the full running order: every driver's position, interval and
 * tyre, for the timing tower.
 */
import useSWR from 'swr'
import { fetcher } from '../lib/api-client'
import type { LatestState } from '../types/api'

export function useRunningOrder() {
  const { data, error, isLoading } = useSWR<LatestState>('/api/state/latest', fetcher, {
    refreshInterval: 2000,
    dedupingInterval: 1000,
    revalidateOnFocus: false,
  })

  return {
    drivers: data?.drivers ?? [],
    isLoading,
    error,
  }
}
