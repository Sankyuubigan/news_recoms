export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  APP_PASSWORD: string;
  SESSION_SECRET: string;
  INGEST_SECRET: string;
  APP_ENV?: string;
}

export type ItemStatus = "pending_ai" | "inbox" | "saved" | "archived" | "rejected";
export type FeedbackValue = "none" | "like" | "dislike";
export type SourceType = "telegram" | "rss" | "youtube";

export interface SourceRow {
  id: number;
  type: SourceType;
  name: string;
  identifier: string;
  category_hint: string | null;
  fetch_full_text: number;
  enabled: number;
  last_fetched_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface ItemRow {
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
  key_points_json: string | null;
  category: string | null;
  tags_json: string | null;
  ai_score: number | null;
  ai_reason: string | null;
  is_ad: number;
  ad_reason: string | null;
  cluster_slug: string | null;
  merged_sources_json: string | null;
  status: ItemStatus;
  feedback: FeedbackValue;
  feedback_reason: string | null;
  feedback_processed: number;
  published_at: string;
  fetched_at: string;
  updated_at: string;
}

export interface FeedItem extends Omit<ItemRow, "key_points_json" | "tags_json" | "merged_sources_json"> {
  key_points: string[];
  tags: string[];
  merged_sources: Array<{ name: string; url: string }>;
  source_name: string;
  source_type: SourceType;
}

export interface IngestItem {
  source_identifier: string;
  external_id?: string | null;
  url: string;
  content_hash: string;
  content_type: "telegram" | "article" | "youtube";
  original_lang?: string;
  title_orig: string;
  content_orig: string;
  media_url?: string | null;
  title_ru?: string | null;
  summary_ru?: string | null;
  key_points?: string[];
  category?: string;
  tags?: string[];
  ai_score?: number | null;
  ai_reason?: string | null;
  is_ad?: boolean;
  ad_reason?: string | null;
  cluster_slug?: string | null;
  merged_sources?: Array<{ name: string; url: string }>;
  status: ItemStatus;
  published_at: string;
}

export interface PipelineRunRow {
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
  provider_stats_json: string | null;
  errors_json: string | null;
  status: "running" | "success" | "partial" | "failed";
}

export interface SessionPayload {
  exp: number;
}
