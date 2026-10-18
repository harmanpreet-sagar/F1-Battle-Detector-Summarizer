/**
 * TypeScript types matching the backend API schemas.
 */

export type DataConfidence = 'high' | 'medium' | 'low'
export type BattleIntensity = 'HOT' | 'WATCH' | 'NONE'
export type TrackStatus = 'green' | 'yellow' | 'red' | 'sc' | 'vsc'
export type ConnectionStatus = 'live' | 'delayed' | 'reconnecting' | 'replay'

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
  /** How battle_score was reached; absent from older backends. */
  score_breakdown?: ScoreBreakdown | null
  /** Championship points that change hands if the chaser passes (7 for the lead, 0 outside the points). */
  points_at_stake?: number
  /** The list order: battle_score boosted x1 (no points) to x2 (the lead) by points_at_stake. */
  rank_score?: number
  intensity: BattleIntensity
  explanation: string
  trend_gap_s: number[]
  trend_timestamps: string[]
  duration_updates: number
  flags: BattleFlags
  // Added by /api/battles/top for display; absent when the driver is unknown.
  chaser_name?: string
  chaser_team?: string
  chaser_acronym?: string | null
  chaser_team_colour?: string | null
  chaser_tyre?: Tyre
  ahead_name?: string
  ahead_team?: string
  ahead_acronym?: string | null
  ahead_team_colour?: string | null
  ahead_tyre?: Tyre
}

/**
 * The battle score's working. gap + closing + pace = raw (weights 0.5, 0.3,
 * 0.2 already applied); score = raw * multiplier, where multiplier is the
 * product of the yellow-flag, pit-window and data-quality penalties.
 */
export interface ScoreBreakdown {
  gap: number
  closing: number
  pace: number
  raw: number
  multiplier: number
  score: number
}

export interface Tyre {
  compound: string | null
  age_laps: number | null
  pit_stops: number
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
  year?: number
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
  name_acronym?: string | null
  /** Hex without '#', as OpenF1 gives it. */
  team_colour?: string | null
  last_lap_time_s?: number
  gap_to_leader_s?: number
  gap_to_ahead_s?: number
  tire_compound?: string
  tire_age_laps?: number
  pit_stops_count: number
  updated_at: string
  gap_updated_at?: string | null
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
  /** Race seconds since lights out. */
  elapsed_s: number | null
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

/** GET /api/state/latest */
export interface LatestState {
  drivers: DriverState[]
  count: number
  updated_at?: string | null
  message?: string
}
