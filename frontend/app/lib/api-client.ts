/**
 * API client for communicating with the backend.
 */

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
    fetcher(`/battles/top?k=${k}&min_intensity=${minIntensity}`),
  
  getCurrentSession: () =>
    fetcher('/session/current'),
  
  getLatestState: () =>
    fetcher('/state/latest'),
  
  getDriverTrend: (driverNumber: number, points: number = 10) =>
    fetcher(`/drivers/${driverNumber}/trend?points=${points}`),
  
  getHealth: () =>
    fetcher('/health'),
}
