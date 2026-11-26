export interface FeedItem {
  id: number;
  source_id: number;
  external_id: string | null;
  url: string;
  content_hash: string;
  content_type: "telegram" | "article" | "youtube";
  original_lang: string | null;
  title_orig: string;
  content_orig: string;
  media_url: string | null;
  title_ru: string | null;
  summary_ru: string | null;
  category: string | null;
  ai_score: number | null;
  ai_reason: string | null;
  is_ad: number;
  ad_reason: string | null;
  cluster_slug: string | null;
  status: "pending_ai" | "inbox" | "saved" | "archived" | "rejected";
  feedback: "none" | "like" | "dislike";
  feedback_reason: string | null;
  feedback_processed: number;
  published_at: string;
  fetched_at: string;
  updated_at: string;
  key_points: string[];
  tags: string[];
  merged_sources: Array<{ name: string; url: string }>;
  source_name: string;
  source_type: "telegram" | "rss" | "youtube";
}

export interface FeedResponse {
  items: FeedItem[];
  total: number;
  counts: { inbox: number; saved: number; archived: number; rejected: number };
  categories: string[];
}

export interface Source {
  id: number;
  type: "telegram" | "rss" | "youtube";
  name: string;
  identifier: string;
  category_hint: string | null;
  fetch_full_text: number;
  enabled: number;
  last_fetched_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface PipelineRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  fetched_count: number;
  ad_blocked_count: number;
  dedup_skipped_count: number;
  ai_processed_count: number;
  inbox_added_count: number;
  rejected_count: number;
  pending_ai_count: number;
  provider_stats: Record<string, { ok?: number; err?: number }>;
  errors: unknown[];
  status: "running" | "success" | "partial" | "failed";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (res.status === 401) {
    throw new UnauthorizedError();
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = JSON.stringify(await res.json());
    } catch {
      detail = await res.text().catch(() => "");
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export class UnauthorizedError extends Error {
  constructor() {
    super("unauthorized");
    this.name = "UnauthorizedError";
  }
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export const api = {
  async login(password: string): Promise<void> {
    await request<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
  },
  async logout(): Promise<void> {
    await request<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
  },
  async me(): Promise<boolean> {
    try {
      const r = await request<{ authenticated: boolean }>("/api/auth/me");
      return r.authenticated;
    } catch {
      return false;
    }
  },

  feed(params: {
    status: string;
    sort: "score" | "date";
    category?: string;
    q?: string;
    limit?: number;
    offset?: number;
  }): Promise<FeedResponse> {
    const sp = new URLSearchParams({
      status: params.status,
      sort: params.sort,
      limit: String(params.limit ?? 30),
      offset: String(params.offset ?? 0),
    });
    if (params.category && params.category !== "Все") sp.set("category", params.category);
    if (params.q) sp.set("q", params.q);
    return request<FeedResponse>(`/api/v1/feed?${sp.toString()}`);
  },

  patchItem(
    id: number,
    patch: { status?: string; feedback?: string; feedback_reason?: string | null },
  ): Promise<{ ok: boolean; item: FeedItem }> {
    return request(`/api/v1/items/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  },

  bulk(action: string, payload: Record<string, unknown>): Promise<{ ok: boolean }> {
    return request(`/api/v1/items/bulk`, {
      method: "POST",
      body: JSON.stringify({ action, ...payload }),
    });
  },

  sources(): Promise<{ sources: Source[] }> {
    return request(`/api/v1/sources`);
  },
  createSource(body: Record<string, unknown>): Promise<{ ok: boolean; source: Source }> {
    return request(`/api/v1/sources`, { method: "POST", body: JSON.stringify(body) });
  },
  patchSource(id: number, body: Record<string, unknown>): Promise<{ ok: boolean; source: Source }> {
    return request(`/api/v1/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  },
  deleteSource(id: number): Promise<{ ok: boolean }> {
    return request(`/api/v1/sources/${id}`, { method: "DELETE" });
  },

  settings(): Promise<{ settings: Record<string, string> }> {
    return request(`/api/v1/settings`);
  },
  saveSettings(body: Record<string, string>): Promise<{ ok: boolean }> {
    return request(`/api/v1/settings`, { method: "PUT", body: JSON.stringify(body) });
  },

  runs(): Promise<{ runs: PipelineRun[] }> {
    return request(`/api/v1/runs`);
  },
};
