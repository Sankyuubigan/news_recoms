/** Типы данных ветки `data`. Должны совпадать с тем, что пишет pipeline/storage.py. */

export type ItemStatus = "pending_ai" | "inbox" | "saved" | "archived" | "rejected";
export type FeedbackValue = "none" | "like" | "dislike";
export type SourceType = "telegram" | "rss" | "youtube";
export type ContentType = "telegram" | "article" | "youtube";

export interface FeedItem {
  id: string;
  source_identifier: string;
  source_name: string;
  source_type: SourceType;
  external_id: string | null;
  url: string;
  content_hash: string;
  content_type: ContentType;
  original_lang: string | null;

  title_orig: string;
  content_orig: string;
  media_url: string | null;

  title_ru: string | null;
  summary_ru: string | null;
  key_points: string[];
  category: string | null;
  tags: string[];
  ai_score: number | null;
  ai_reason: string | null;
  is_ad: boolean;
  ad_reason: string | null;

  cluster_slug: string | null;
  merged_sources: Array<{ name: string; url: string }>;

  status: ItemStatus;
  feedback: FeedbackValue;
  feedback_reason: string | null;
  feedback_processed: boolean;

  published_at: string;
  fetched_at: string;
  updated_at: string;
}

/** Только действия пользователя — то, что пишет фронтенд. */
export interface UserStateItem {
  status?: ItemStatus;
  feedback?: FeedbackValue;
  feedback_reason?: string | null;
  feedback_processed?: boolean;
  updated_at: string;
}

export interface UserState {
  updated_at: string;
  items: Record<string, UserStateItem>;
}

export interface Source {
  id: number;
  type: SourceType;
  name: string;
  identifier: string;
  category_hint: string;
  fetch_full_text: number;
  enabled: number;
  last_fetched_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface DataConfig {
  updated_at: string;
  next_source_id: number;
  sources: Source[];
  settings: Record<string, string>;
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

export interface RunsFile {
  updated_at: string;
  pipeline_runs: PipelineRun[];
}

export interface FeedResponse {
  items: FeedItem[];
  total: number;
  counts: {
    inbox: number;
    saved: number;
    archived: number;
    rejected: number;
    /** Собраны, но ещё не прогнаны через LLM (нет ключа или все провайдеры легли). */
    pending_ai: number;
  };
  categories: string[];
}

export interface SyncStatus {
  state: "idle" | "pending" | "saving" | "saved" | "error" | "readonly";
  message?: string;
}
