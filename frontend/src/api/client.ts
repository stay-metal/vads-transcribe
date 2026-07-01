import type { Job, TranscriptResult, TrackRef, UploadResult } from "./types";

// Глобальный обработчик истёкшей сессии (регистрируется в AuthProvider).
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

// Same-origin: cookie HttpOnly+SameSite=Strict ходит автоматически (без CORS).
async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* not json */
    }
    // 401 на любом /api (кроме самого me) → сброс сессии и редирект на логин.
    if (res.status === 401 && !path.endsWith("/auth/me")) onUnauthorized?.();
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const api = {
  async login(username: string, password: string): Promise<{ user: string }> {
    const form = new URLSearchParams({ username, password });
    return req("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form,
    });
  },
  logout: () => req<void>("/api/auth/logout", { method: "POST" }),
  me: () => req<{ user: string }>("/api/auth/me"),

  // Готовность тёплой модели (200 → true, 503 → false). Не кидает на 503.
  async ready(): Promise<boolean> {
    try {
      const r = await fetch("/readyz", { credentials: "same-origin" });
      return r.ok;
    } catch {
      return false;
    }
  },

  upload(files: File[]): Promise<UploadResult> {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return req("/api/uploads", { method: "POST", body: fd });
  },

  discoverTracks: (recId: string) =>
    req<{ recording_id: string; kind: string; tracks: TrackRef[] }>(
      `/api/recordings/${recId}/discover-tracks`,
    ),
  confirmTracks: (recId: string, tracks: TrackRef[]) =>
    req<{ recording_id: string; tracks: TrackRef[] }>(
      `/api/recordings/${recId}/discover-tracks`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tracks }),
      },
    ),

  submitJob: (body: Record<string, unknown>) =>
    req<{ job_id: string; state: string; mode: string }>("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  listJobs: () => req<{ jobs: Job[] }>("/api/jobs"),
  getJob: (id: string) => req<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => req<unknown>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  result: (id: string) => req<TranscriptResult>(`/api/jobs/${id}/result`),
  putSpeakers: (id: string, edits: Record<string, string>) =>
    req<unknown>(`/api/jobs/${id}/speakers`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edits }),
    }),
  audioUrl: (id: string) => `/api/jobs/${id}/audio`,
  downloadUrl: (id: string, format: string) =>
    `/api/jobs/${id}/download?format=${format}`,

  // --- Глоссарий (канонизация имён/терминов, страж I1) ---
  getGlossary: () => req<Glossary>("/api/glossary"),
  putGlossary: (body: Glossary) =>
    req<Glossary>("/api/glossary", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // --- Яндекс.Диск (ручной ingestion) ---
  yandexStatus: () => req<YandexStatus>("/api/yandex/status"),
  putYandexToken: (token: string) =>
    req<YandexStatus>("/api/yandex/token", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }),
  yandexBrowse: (path: string) =>
    req<{ path: string; entries: YaEntry[] }>(
      `/api/yandex/browse?path=${encodeURIComponent(path)}`,
    ),
  yandexPull: (path: string) =>
    req<{ status: string; surrogate_id?: string; kind?: string }>("/api/yandex/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),

  // --- Галереи голосов (voiceprint) ---
  listGalleries: () => req<{ galleries: Gallery[] }>("/api/galleries"),
  createGallery: (name: string, files: File[]) => {
    const fd = new FormData();
    fd.append("name", name);
    files.forEach((f) => fd.append("files", f));
    return req<{ building: string; voices: string[] }>("/api/galleries", { method: "POST", body: fd });
  },
  deleteGallery: (name: string) =>
    req<{ deleted: string }>(`/api/galleries/${encodeURIComponent(name)}`, { method: "DELETE" }),

  // --- Авто-watch источника (периодический опрос watch_dir) ---
  getIngestSource: () => req<IngestSource>("/api/ingest/source"),
  putIngestSource: (body: { watch_dir: string; enabled: boolean; poll_interval: number }) =>
    req<{ configured: boolean; watch_dir: string; enabled: boolean }>("/api/ingest/source", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};

export interface IngestSource {
  configured: boolean;
  watch_dir?: string;
  enabled?: boolean;
  poll_interval?: number;
  default_params?: Record<string, unknown>;
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
}
export interface YaEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number | null;
}
