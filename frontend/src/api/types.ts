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
  done_duration_sec: number;
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
