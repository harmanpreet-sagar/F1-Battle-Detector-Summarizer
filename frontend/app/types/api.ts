/**
 * TypeScript types matching the backend API schemas.
 */

export type DataConfidence = 'high' | 'medium' | 'low'
export type BattleIntensity = 'HOT' | 'WATCH' | 'NONE'
export type TrackStatus = 'green' | 'yellow' | 'red' | 'sc' | 'vsc'
export type ConnectionStatus = 'live' | 'delayed' | 'reconnecting'

export interface Battle {
  battle_id: string
  chaser_driver_number: number
  ahead_driver_number: number
  chaser_position: number
  ahead_position: number
  gap_now_s: number
  closing_rate_s_per_s?: number
  pace_delta_s_per_lap?: number
  battle_score: number
  intensity: BattleIntensity
  explanation: string
  trend_gap_s: number[]
  trend_timestamps: string[]
  duration_updates: number
  flags: BattleFlags
}

export interface BattleFlags {
  pit_window_active: boolean
  under_yellow: boolean
  data_quality_warning: boolean
}

export interface SessionStatus {
  session_key: number
  session_name: string
  session_type: string
  session_status: 'started' | 'finished' | 'aborted'
  circuit_short_name: string
  meeting_name: string
  current_lap?: number
  total_laps?: number
  track_status?: TrackStatus
  gmt_offset: string
  updated_at: string
}

export interface DriverState {
  driver_number: number
  full_name: string
  team_name: string
  position: number
  last_lap_time_s?: number
  gap_to_leader_s?: number
  gap_to_ahead_s?: number
  tire_compound?: string
  tire_age_laps?: number
  pit_stops_count: number
  updated_at: string
  data_confidence: DataConfidence
}

export type DataMode = 'live' | 'replay' | 'mock'

export interface ReplayRace {
  session_key: number
  meeting_name: string
  circuit: string
  year?: number
}

/** GET /api/replay/status. Outside replay mode every field but `mode` is null. */
export interface ReplayStatus {
  mode: DataMode
  session_key: number | null
  meeting_name: string | null
  circuit: string | null
  year: number | null
  race_time: string | null
  lap: number | null
  total_laps: number | null
  track_status: TrackStatus | null
  stoppage: boolean | null
  speed: number | null
  progress_pct: number | null
  next_race: ReplayRace | null
}

/** One cached race, from GET /api/replay/sessions. */
export interface ReplaySession {
  session_key: number
  year?: number
  round?: number
  circuit?: string
  meeting_name?: string
  session_name?: string
  date_start?: string
  rows: number
  bytes: number
  schema_version: number
  in_playlist: boolean
}
