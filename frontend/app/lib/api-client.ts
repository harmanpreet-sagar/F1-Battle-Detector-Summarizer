/**
 * API client for communicating with the backend.
 */

import type { ReplaySession, ReplayStatus } from '../types/api'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

export async function fetcher(url: string) {
  const response = await fetch(`${API_BASE_URL}${url}`)
  
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`)
  }
  
  return response.json()
}

export const apiClient = {
  getBattles: (k: number = 5, minIntensity: string = 'WATCH') =>
    fetcher(`/api/battles/top?k=${k}&min_intensity=${minIntensity}`),

  getCurrentSession: () =>
    fetcher('/api/session/current'),

  getLatestState: () =>
    fetcher('/api/state/latest'),

  getDriverTrend: (driverNumber: number, points: number = 10) =>
    fetcher(`/api/drivers/${driverNumber}/trend?points=${points}`),

  getHealth: () =>
    fetcher('/api/health'),

  getReplayStatus: (): Promise<ReplayStatus> =>
    fetcher('/api/replay/status'),

  getReplaySessions: (): Promise<{ sessions: ReplaySession[] }> =>
    fetcher('/api/replay/sessions'),
}
