export type Meta = {
  count: number;
  data_as_of: string | null;
  last_synced_at: string | null;
  next_sync_at: string | null;
  stale: boolean;
  sync_status?: string;
  sync_error?: string | null;
  source?: string;
};

export type Envelope<T> = { status: "ok"; data: T; meta: Meta };

export type SummaryRow = {
  date: string;
  steps: number;
  distance_m: number;
  active_kcal: number;
};

export type SleepRow = {
  sleep_id: string;
  start_at: string;
  end_at: string;
  duration_minutes: number;
  time_asleep_minutes: number;
  avg_hrv_ms: number | null;
  hrv_median_ms: number | null;
  hrv_min_ms: number | null;
  hrv_max_ms: number | null;
  is_nap: number;
};

export type Sample = {
  timestamp: string;
  bpm?: number;
  spo2_pct?: number;
  stress_score?: number;
};

export type KeyRow = {
  id: string;
  prefix: string;
  label: string | null;
  scopes: string;
  created_at: string;
  last_used_at: string | null;
  revoked: number;
};

export type SyncState = {
  data_type: string;
  last_synced_at: string | null;
  last_record_at: string | null;
  status: string;
  error: string | null;
};

export type StatusData = {
  configured: boolean;
  user_id?: string;
  region?: string;
  sync: SyncState[];
  stale: Record<string, boolean>;
};

export type SyncJob = {
  id: string;
  data_type: string;
  status: string;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
};

export type AuthData = { authenticated: boolean; csrf_token: string | null };
export type CreatedKey = { token: string; prefix: string; label: string | null; scopes: string[] };
