/**
 * SWR hook for the replay broadcast's position.
 *
 * Safe to call in every mode: outside replay the backend answers with only
 * `mode` set, which is how the page decides what header to draw.
 */
import useSWR from 'swr'
import { fetcher } from '../lib/api-client'
import type { ReplayStatus } from '../types/api'

export function useReplayStatus() {
  const { data, error } = useSWR<ReplayStatus>('/api/replay/status', fetcher, {
    refreshInterval: 2000,
    revalidateOnFocus: false,
  })

  return {
    status: data,
    isReplay: data?.mode === 'replay',
    error,
  }
}
