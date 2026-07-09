import type {
  Job,
  JobsPage,
  TranscriptResult,
  TrackRef,
  UploadResult,
  SourceType,
  ScanProfileT,
  ScanPreset,
  FsBrowse,
  IngestSource,
  Gallery,
  Glossary,
  YandexStatus,
  YaEntry,
} from "./types";

// Реэкспорт доменных типов — обратная совместимость импортов из "@/api/client".
export type {
  SourceType,
  ScanOutputT,
  ScanProfileT,
  ScanPreset,
  FsBrowse,
  IngestSource,
  Gallery,
  Glossary,
  YandexStatus,
  YaEntry,
} from "./types";

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
  listJobs: (params?: {
    q?: string;
    scope?: string;
    date_from?: string;
    date_to?: string;
    limit?: number;
    offset?: number;
  }) => {
    const sp = new URLSearchParams();
    if (params?.q) sp.set("q", params.q);
    if (params?.scope) sp.set("scope", params.scope);
    if (params?.date_from) sp.set("date_from", params.date_from);
    if (params?.date_to) sp.set("date_to", params.date_to);
    if (params?.limit) sp.set("limit", String(params.limit));
    if (params?.offset) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return req<JobsPage>(`/api/jobs${qs ? `?${qs}` : ""}`);
  },
  getJob: (id: string) => req<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => req<unknown>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  pauseJob: (id: string) => req<unknown>(`/api/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) => req<unknown>(`/api/jobs/${id}/resume`, { method: "POST" }),
  rerunJob: (id: string) => req<{ job_id: string }>(`/api/jobs/${id}/rerun`, { method: "POST" }),
  result: (id: string) => req<TranscriptResult>(`/api/jobs/${id}/result`),
  putSpeakers: (id: string, edits: Record<string, string>) =>
    req<unknown>(`/api/jobs/${id}/speakers`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edits }),
    }),
  putSegmentText: (id: string, index: number, text: string) =>
    req<unknown>(`/api/jobs/${id}/segments`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index, text }),
    }),
  writeTranscript: (id: string, format?: string) =>
    req<{ written: string; format: string }>(
      `/api/jobs/${id}/write${format ? `?format=${format}` : ""}`,
      { method: "POST" },
    ),
  getSettings: () => req<{ transcript_format: string }>("/api/settings"),
  putSettings: (transcript_format: string) =>
    req<{ transcript_format: string }>("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript_format }),
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

  // --- Авто-watch источников (yandex — облако, local — папка Zoom-выгрузок) ---
  getIngestSource: (sourceType: SourceType = "yandex") =>
    req<IngestSource>(`/api/ingest/source?source_type=${sourceType}`),
  putIngestSource: (body: {
    watch_dir: string;
    enabled: boolean;
    poll_interval: number;
    source_type?: SourceType;
    scan_profile?: ScanProfileT;
  }) =>
    req<{ configured: boolean; watch_dir: string; enabled: boolean }>("/api/ingest/source", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  localScanNow: () =>
    req<{ scanned: boolean; started: { job_id: string; kind: string; part: number }[] }>(
      "/api/ingest/local/scan",
      { method: "POST" },
    ),

  // --- Серверный браузер каталогов (выбор папки из UI) ---
  fsBrowse: (path: string) =>
    req<FsBrowse>(`/api/fs/browse?path=${encodeURIComponent(path)}`),

  // --- Пресеты раскладки источника ---
  listScanPresets: () => req<{ presets: ScanPreset[] }>("/api/scan-presets"),
  createScanPreset: (name: string, body: ScanProfileT) =>
    req<{ id: string; name: string }>("/api/scan-presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, body }),
    }),
  deleteScanPreset: (id: string) =>
    req<void>(`/api/scan-presets/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
