export type JobState =
  | "queued"
  | "preclean"
  | "vad"
  | "diarization"
  | "asr"
  | "quality"
  | "formatting"
  | "done"
  | "error"
  | "canceled";

export interface Job {
  id: string;
  mode: "route_a" | "single";
  state: JobState;
  stage_pct: number;
  error_code: string | null;
  error_message: string | null;
  device_fallback: boolean;
  duration_sec: number | null;
  created_at: string;
  started_at?: string | null;
  finished_at: string | null;
  source?: string | null;
  title?: string | null;
  track_count?: number | null;
  queue_position?: number | null;
}

export interface JobsPage {
  jobs: Job[];
  total: number;
  counts: { active: number; queued: number; done: number; error: number; canceled: number };
  avg_rtf: number | null;
  done_duration_sec: number; // контракт API: сервер шлёт, фронт пока не отображает
}

export interface TrackRef {
  id: number;
  name: string;
}

export interface UploadResult {
  recording_id: string;
  kind: "route_a" | "single";
  tracks: { name: string; size: number }[];
}

export interface Segment {
  text: string;
  start: number;
  end: number;
  speaker: string | null;
  original_speaker?: string | null; // стабильный сырой ярлык — ключ правки
  original_text?: string | null; // текст до ручной правки (overlay)
  confidence?: number | null;
  speaker_confidence?: number | null;
  provenance?: string;
  flags?: string[];
}

export interface TranscriptResult {
  metadata: Record<string, unknown> & {
    duration?: number;
    model?: string;
    device_fallback?: boolean;
    speakers_count?: number;
  };
  segments: Segment[];
  full_text: string;
}

export type SourceType = "yandex" | "local";

export interface ScanOutputT {
  mode: "beside" | "fixed";
  subdir: string;
  dir: string | null;
}

export interface ScanProfileT {
  layout: "zoom" | "plain";
  tracks_subdir: string | null;
  track_mode: "combine" | "separate" | "mix_only";
  parts_mode: "merge" | "separate";
  media_suffixes: string[];
  skip_dirs: string[];
  output: ScanOutputT;
}

export interface ScanPreset {
  id: string;
  name: string;
  builtin: boolean;
  body: ScanProfileT;
}

export interface FsBrowse {
  path: string;
  parent: string | null;
  dirs: { name: string; path: string }[];
  denied: boolean;
}

export interface IngestSource {
  configured: boolean;
  source_type?: SourceType;
  watch_dir?: string;
  enabled?: boolean;
  poll_interval?: number;
  default_params?: Record<string, unknown>;
  scan_profile?: Partial<ScanProfileT>;
  last_scan_at?: string | null;
}

export interface Gallery {
  name: string;
  voices: string[];
}

export interface Glossary {
  people: Record<string, string>;
  terms: Record<string, string>;
}

export interface YandexStatus {
  connected: boolean;
  check_ok: boolean;
  oauth_available?: boolean;
}

export interface YaEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number | null;
}
