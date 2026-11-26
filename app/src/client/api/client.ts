/**
 * Публичный фасад данных для компонентов.
 * Работает поверх `store` (GitHub-ветка `data`) и сохраняет прежние сигнатуры.
 */

import { dispatchIngest, getRepo, getToken, type RepoRef } from "./github";
import { store, nowIso } from "./store";
import type {
  DataConfig,
  FeedItem,
  FeedResponse,
  ItemStatus,
  PipelineRun,
  Source,
  SourceType,
  SyncStatus,
} from "./types";

export type {
  DataConfig,
  FeedItem,
  FeedResponse,
  ItemStatus,
  PipelineRun,
  Source,
  SourceType,
  SyncStatus,
};
export { store, getRepo, getToken };
export type { RepoRef };

export const api = {
  // ---------------------------------------------------------------- чтение

  async feed(params: {
    status: string;
    sort: "score" | "date";
    category?: string;
    q?: string;
    limit?: number;
    offset?: number;
  }): Promise<FeedResponse> {
    await store.load();
    return store.getFeed(params);
  },

  async sources(): Promise<{ sources: Source[] }> {
    await store.load();
    return { sources: store.getSources() };
  },

  async settings(): Promise<{ settings: Record<string, string> }> {
    await store.load();
    return { settings: store.getSettings() };
  },

  async runs(): Promise<{ runs: PipelineRun[] }> {
    await store.load();
    return { runs: store.getRuns() };
  },

  // --------------------------------------------------------------- действия

  patchItem(
    id: string,
    patch: { status?: ItemStatus; feedback?: string; feedback_reason?: string | null },
  ): { ok: boolean; item: FeedItem | null } {
    store.patchItem(id, patch);
    return { ok: true, item: null };
  },

  bulk(action: string, payload: Record<string, unknown>): { ok: boolean; updated: number } {
    if (action === "archive_ids") {
      const ids = (payload.ids as string[] | undefined) ?? [];
      store.bulkArchive(ids);
      return { ok: true, updated: ids.length };
    }
    if (action === "archive_older_than") {
      const days = Number(payload.days ?? 3);
      return { ok: true, updated: store.bulkArchiveOlderThan(Number.isFinite(days) ? days : 3) };
    }
    return { ok: true, updated: 0 };
  },

  // --------------------------------------------------------------- источники

  async createSource(body: {
    type: SourceType;
    identifier: string;
    name?: string;
    category_hint?: string;
    fetch_full_text?: boolean;
  }): Promise<{ ok: boolean; source: Source }> {
    await store.load();
    const config = store.getConfig();
    if (!config) throw new Error("config.json не загружен — проверь репозиторий и ветку data");

    const identifier = normalizeIdentifier(body.type, body.identifier);
    if (!identifier) throw new Error("Пустой идентификатор источника");

    if (config.sources.some((s) => s.identifier === identifier)) {
      throw new Error("Такой источник уже добавлен");
    }

    const nextId = Math.max(config.next_source_id ?? 1, ...config.sources.map((s) => s.id + 1));
    const source: Source = {
      id: nextId,
      type: body.type,
      name: (body.name ?? "").trim() || identifier,
      identifier,
      category_hint: body.category_hint ?? "",
      fetch_full_text: body.fetch_full_text ? 1 : 0,
      enabled: 1,
      last_fetched_at: null,
      last_error: null,
      created_at: nowIso(),
    };

    store.saveConfig({
      ...config,
      next_source_id: nextId + 1,
      sources: [...config.sources, source],
    });
    return { ok: true, source };
  },

  async patchSource(id: number, body: Record<string, unknown>): Promise<{ ok: boolean }> {
    await store.load();
    const config = store.getConfig();
    if (!config) throw new Error("config.json не загружен");

    const sources = config.sources.map((s) => {
      if (s.id !== id) return s;
      const next: Source = { ...s };
      if (body.name !== undefined) next.name = String(body.name).trim() || s.name;
      if (body.identifier !== undefined) {
        next.identifier = normalizeIdentifier(s.type, String(body.identifier));
      }
      if (body.category_hint !== undefined) next.category_hint = String(body.category_hint);
      if (body.fetch_full_text !== undefined) next.fetch_full_text = body.fetch_full_text ? 1 : 0;
      if (body.enabled !== undefined) next.enabled = body.enabled ? 1 : 0;
      return next;
    });

    store.saveConfig({ ...config, sources });
    return { ok: true };
  },

  async deleteSource(id: number): Promise<{ ok: boolean }> {
    await store.load();
    const config = store.getConfig();
    if (!config) throw new Error("config.json не загружен");
    store.saveConfig({ ...config, sources: config.sources.filter((s) => s.id !== id) });
    return { ok: true };
  },

  // ---------------------------------------------------------------- настройки

  async saveSettings(body: Record<string, string>): Promise<{ ok: boolean }> {
    await store.load();
    const config = store.getConfig();
    if (!config) throw new Error("config.json не загружен");
    store.saveConfig({ ...config, settings: { ...config.settings, ...body } });
    await store.flush();
    return { ok: true };
  },

  // ------------------------------------------------------------- синхронизация

  async connect(token: string): Promise<string> {
    store.setCredentials(token, getRepo());
    await store.load(true);
    return store.getRepoLabel();
  },

  async disconnect(): Promise<void> {
    store.setCredentials(null);
  },

  async runIngest(): Promise<void> {
    const repo = getRepo();
    const token = getToken();
    if (!repo || !token) throw new Error("Сначала подключи синхронизацию");
    await dispatchIngest(repo, token);
  },

  async refresh(): Promise<void> {
    await store.load(true);
  },
};

// ------------------------------------------------------------- normalization

/** Нормализует identifier по типу источника (та же логика, что была в worker/sources.ts). */
export function normalizeIdentifier(type: SourceType, raw: string): string {
  const v = (raw ?? "").trim();
  if (type === "telegram") {
    return v
      .replace(/^https?:\/\/(www\.)?t\.me\//i, "")
      .replace(/^@/, "")
      .replace(/^s\//i, "")
      .replace(/\/+$/, "")
      .trim();
  }
  if (type === "youtube") {
    const byChannel = /[?&]channel_id=([A-Za-z0-9_-]+)/.exec(v);
    if (byChannel) return byChannel[1];
    if (/^UC[A-Za-z0-9_-]{20,}$/.test(v)) return v;
    const at = /@([A-Za-z0-9._-]+)/.exec(v);
    if (at) return `@${at[1]}`;
    return v;
  }
  return v;
}
