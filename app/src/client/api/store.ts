/**
 * Хранилище ленты на клиенте.
 *
 * Данные читаются из ветки `data` (GitHub), мержатся на лету
 * (`user_state.json` приоритетнее `feed.json`), а действия пользователя
 * копятся в памяти и улетают одним коммитом с debounce 2 секунды.
 */

import {
  ConflictError,
  getRepo,
  getToken,
  readJson,
  writeJson,
  type RepoRef,
} from "./github";
import type {
  ContentKind,
  DataConfig,
  FeedItem,
  FeedResponse,
  ItemStatus,
  RunsFile,
  SyncStatus,
  UserState,
  UserStateItem,
} from "./types";

const LS_PENDING = "nr.pendingUserState";
const LS_CACHE = "nr.cache";
const SYNC_DEBOUNCE_MS = 2000;
const MAX_RETRIES = 3;

interface CacheShape {
  feed: FeedItem[];
  userState: UserState;
  config: DataConfig | null;
  runs: RunsFile | null;
  fetchedAt: string;
}

type Listener = () => void;

// ------------------------------------------------------------ pending queue

function loadPending(): Record<string, UserStateItem> {
  try {
    const raw = localStorage.getItem(LS_PENDING);
    return raw ? (JSON.parse(raw) as Record<string, UserStateItem>) : {};
  } catch {
    return {};
  }
}

function savePending(items: Record<string, UserStateItem>): void {
  try {
    if (Object.keys(items).length) localStorage.setItem(LS_PENDING, JSON.stringify(items));
    else localStorage.removeItem(LS_PENDING);
  } catch {
    /* ignore */
  }
}

function loadCache(): CacheShape | null {
  try {
    const raw = localStorage.getItem(LS_CACHE);
    return raw ? (JSON.parse(raw) as CacheShape) : null;
  } catch {
    return null;
  }
}

function saveCache(cache: CacheShape): void {
  try {
    localStorage.setItem(LS_CACHE, JSON.stringify(cache));
  } catch {
    /* переполнение квоты — не критично */
  }
}

export function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function itemKind(item: { kind?: ContentKind }): ContentKind {
  return item.kind === "repos" ? "repos" : "news";
}

// ------------------------------------------------------------------- store

class FeedStore {
  private repo: RepoRef | null = null;
  private token: string | null = null;

  private feed: FeedItem[] = [];
  private userState: UserState = { updated_at: nowIso(), items: {} };
  private config: DataConfig | null = null;
  private runs: RunsFile | null = null;

  private userStateSha = "";
  private configSha = "";

  private pending: Record<string, UserStateItem> = loadPending();
  private pendingConfig: DataConfig | null = null;

  private syncTimer: ReturnType<typeof setTimeout> | null = null;
  private syncStatus: SyncStatus = { state: "idle" };
  private listeners = new Set<Listener>();

  private loaded = false;
  private loading: Promise<void> | null = null;
  private flushing: Promise<void> | null = null;
  private reflushRequested = false;

  // --------------------------------------------------------------- lifecycle

  constructor() {
    if (typeof window !== "undefined") {
      const flush = () => void this.flush();
      window.addEventListener("pagehide", flush);
      window.addEventListener("beforeunload", flush);
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") flush();
      });
      window.addEventListener("online", () => void this.flush());
    }
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private emit(): void {
    for (const l of this.listeners) l();
  }

  getStatus(): SyncStatus {
    return this.syncStatus;
  }

  private setStatus(state: SyncStatus["state"], message?: string): void {
    this.syncStatus = { state, message };
    this.emit();
  }

  hasToken(): boolean {
    return Boolean(this.token);
  }

  getRepoLabel(): string {
    return this.repo ? `${this.repo.owner}/${this.repo.repo}` : "—";
  }

  /** Смена токена/репозитория на лету (после подключения синхронизации). */
  setCredentials(token: string | null, repo?: RepoRef | null): void {
    this.token = token;
    if (repo) this.repo = repo;
    this.setStatus(token ? "idle" : "readonly");
  }

  // ------------------------------------------------------------------- load

  async load(force = false): Promise<void> {
    if (this.loading && !force) return this.loading;
    if (this.loaded && !force) return;

    this.loading = this.doLoad();
    try {
      await this.loading;
    } finally {
      this.loading = null;
    }
  }

  private async doLoad(): Promise<void> {
    this.repo = getRepo();
    this.token = getToken();

    // Мгновенный рендер из кэша, пока идёт сеть
    const cached = loadCache();
    if (cached) {
      this.feed = cached.feed ?? [];
      this.userState = cached.userState ?? this.userState;
      this.config = cached.config;
      this.runs = cached.runs;
      this.emit();
    }

    if (!this.repo) {
      // Локальный режим: применяем накопленные действия к кэшу, чтобы UI был консистентен
      this.mergePendingIntoUserState();
      this.loaded = true;
      this.setStatus("readonly", "Не удалось определить репозиторий");
      this.emit();
      return;
    }

    if (!this.token) this.setStatus("readonly");

    try {
      const [feedFile, stateFile, configFile, runsFile] = await Promise.all([
        readJson<FeedItem[]>(this.repo, "feed.json", this.token),
        readJson<UserState>(this.repo, "user_state.json", this.token),
        readJson<DataConfig>(this.repo, "config.json", this.token),
        readJson<RunsFile>(this.repo, "runs.json", this.token),
      ]);

      this.feed = feedFile?.data ?? [];
      this.userState = stateFile?.data ?? { updated_at: nowIso(), items: {} };
      this.userStateSha = stateFile?.sha ?? "";
      this.config = configFile?.data ?? null;
      this.configSha = configFile?.sha ?? "";
      this.runs = runsFile?.data ?? null;

      // Локальные несинхронизированные действия применяем поверх серверных
      this.mergePendingIntoUserState();

      this.loaded = true;
      this.persistCache();
      this.emit();

      if (Object.keys(this.pending).length) this.scheduleSync();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      this.setStatus("error", msg);
      this.loaded = true;
      this.emit();
    }
  }

  // ------------------------------------------------------------- merge logic

  private mergePendingIntoUserState(): void {
    for (const [id, entry] of Object.entries(this.pending)) {
      const server = this.userState.items[id];
      if (!server || (server.updated_at ?? "") < entry.updated_at) {
        this.userState.items[id] = entry;
      }
    }
  }

  /** Сливает feed.json и user_state.json. Возвращает готовые к отображению элементы. */
  getMerged(): FeedItem[] {
    return this.feed.map((item) => {
      const us = this.userState.items[item.id];
      if (!us) return item;
      if (us.updated_at && item.updated_at && us.updated_at < item.updated_at) return item;
      return {
        ...item,
        status: us.status ?? item.status,
        feedback: us.feedback ?? item.feedback,
        feedback_reason:
          us.feedback_reason !== undefined ? us.feedback_reason : item.feedback_reason,
        feedback_processed: us.feedback_processed ?? item.feedback_processed,
      };
    });
  }

  /**
   * Фильтрация вкладки — ОДИН источник правды для getFeed и getFeedIds.
   * Иначе «показать 60» и «отметить всё» начнут расходиться.
   */
  private filtered(params: {
    status: string;
    kind?: ContentKind | "all";
    sort?: "score" | "date";
    category?: string;
    q?: string;
  }): FeedItem[] {
    let filtered = this.getMerged().filter(
      (it) => it.status !== "pending_ai" && it.status === params.status,
    );

    if (params.kind && params.kind !== "all") {
      filtered = filtered.filter((it) => itemKind(it) === params.kind);
    }

    if (params.category && params.category !== "Все") {
      filtered = filtered.filter((it) => it.category === params.category);
    }

    const q = (params.q ?? "").trim().toLowerCase();
    if (q) {
      filtered = filtered.filter(
        (it) =>
          (it.title_ru ?? "").toLowerCase().includes(q) ||
          (it.title_orig ?? "").toLowerCase().includes(q) ||
          (it.summary_ru ?? "").toLowerCase().includes(q) ||
          (it.repo_url ?? "").toLowerCase().includes(q) ||
          (it.interface_type ?? "").toLowerCase().includes(q),
      );
    }

    const sort = params.sort ?? "score";
    filtered.sort((a, b) => {
      if (sort === "date") return (b.published_at ?? "").localeCompare(a.published_at ?? "");
      const ds = (b.ai_score ?? 0) - (a.ai_score ?? 0);
      return ds !== 0 ? ds : (b.published_at ?? "").localeCompare(a.published_at ?? "");
    });

    return filtered;
  }

  getFeed(params: {
    status: string;
    kind?: ContentKind | "all";
    sort: "score" | "date";
    category?: string;
    q?: string;
    limit?: number;
    offset?: number;
  }): FeedResponse {
    const all = this.getMerged();

    const counts = {
      inbox: 0,
      news_inbox: 0,
      repos_inbox: 0,
      saved: 0,
      archived: 0,
      rejected: 0,
      pending_ai: 0,
    };
    for (const it of all) {
      if (it.status === "inbox") {
        counts.inbox += 1;
        if (itemKind(it) === "repos") counts.repos_inbox += 1;
        else counts.news_inbox += 1;
      } else if (it.status in counts) {
        counts[it.status as keyof typeof counts] += 1;
      }
    }

    const categories = Array.from(
      new Set(
        all
          .filter(
            (it) =>
              it.status === params.status &&
              (!params.kind || params.kind === "all" || itemKind(it) === params.kind) &&
              it.category &&
              it.category !== "Общее",
          )
          .map((it) => it.category as string),
      ),
    ).slice(0, 20);

    const filtered = this.filtered(params);
    const offset = params.offset ?? 0;
    const limit = params.limit ?? 60;

    return {
      items: filtered.slice(offset, offset + limit),
      total: filtered.length,
      counts,
      categories,
    };
  }

  /** ID ВСЕХ постов вкладки с учётом фильтров — для кнопки «Всё просмотрено». */
  getFeedIds(params: { status: string; kind?: ContentKind | "all"; category?: string; q?: string }): string[] {
    return this.filtered(params).map((it) => it.id);
  }

  getConfig(): DataConfig | null {
    return this.config;
  }

  getRuns() {
    return this.runs?.pipeline_runs ?? [];
  }

  getSettings(): Record<string, string> {
    return { ...(this.config?.settings ?? {}) };
  }

  getSources() {
    return [...(this.config?.sources ?? [])].sort(
      (a, b) => a.type.localeCompare(b.type) || a.name.localeCompare(b.name),
    );
  }

  // ---------------------------------------------------------------- mutations

  /** Мгновенно применяет действие пользователя и планирует синхронизацию. */
  patchItem(
    id: string,
    patch: { status?: ItemStatus; feedback?: string; feedback_reason?: string | null },
  ): void {
    const ts = nowIso();
    const entry: UserStateItem = {
      ...(this.userState.items[id] ?? {}),
      updated_at: ts,
    };
    if (patch.status) entry.status = patch.status;
    if (patch.feedback) entry.feedback = patch.feedback as UserStateItem["feedback"];
    if (patch.feedback_reason !== undefined) entry.feedback_reason = patch.feedback_reason;
    if (patch.feedback) entry.feedback_processed = false;

    this.userState.items[id] = entry;
    this.pending[id] = entry;
    savePending(this.pending);
    this.persistCache();
    this.emit();

    if (this.token) this.scheduleSync();
  }

  bulkArchive(ids: string[]): void {
    const ts = nowIso();
    for (const id of ids) {
      const entry: UserStateItem = {
        ...(this.userState.items[id] ?? {}),
        status: "archived",
        updated_at: ts,
      };
      this.userState.items[id] = entry;
      this.pending[id] = entry;
    }
    savePending(this.pending);
    this.persistCache();
    this.emit();
    if (this.token) this.scheduleSync();
  }

  bulkArchiveOlderThan(days: number, kind?: ContentKind | "all"): number {
    const cutoff = Date.now() - days * 86400_000;
    const ids = this.getMerged()
      .filter(
        (it) =>
          it.status === "inbox" &&
          (!kind || kind === "all" || itemKind(it) === kind) &&
          new Date(it.published_at).getTime() < cutoff,
      )
      .map((it) => it.id);
    if (ids.length) this.bulkArchive(ids);
    return ids.length;
  }

  /**
   * Чистит «Мусор ИИ»: переводит отсечённое в `archived`.
   *
   * Почему `archived`, а не удаление из файла: `user_state.json` — это
   * оверлей действий пользователя, физически удалять оттуда записи значит
   * ломать синхронизацию (другой клиент вернёт их обратно). Пайплайн сам
   * вычистит `rejected` через REJECTED_TTL_DAYS = 7.
   */
  bulkDismissRejected(olderThanDays = 0): number {
    const cutoff = Date.now() - olderThanDays * 86400_000;
    const ids = this.getMerged()
      .filter((it) => {
        if (it.status !== "rejected") return false;
        if (olderThanDays <= 0) return true;
        return new Date(it.published_at).getTime() < cutoff;
      })
      .map((it) => it.id);
    if (ids.length) this.bulkArchive(ids);
    return ids.length;
  }

  /** Сохраняет источники/настройки в config.json (с debounce и обработкой 409). */
  saveConfig(next: DataConfig): void {
    this.config = { ...next, updated_at: nowIso() };
    this.pendingConfig = this.config;
    this.persistCache();
    this.emit();
    if (this.token) this.scheduleSync();
  }

  // ------------------------------------------------------------------- sync

  private scheduleSync(): void {
    this.setStatus("pending");
    if (this.syncTimer) clearTimeout(this.syncTimer);
    this.syncTimer = setTimeout(() => void this.flush(), SYNC_DEBOUNCE_MS);
  }

  /** Принудительно отправляет накопленное (уход со вкладки, кнопка «Сохранить»). */
  async flush(): Promise<void> {
    if (this.syncTimer) {
      clearTimeout(this.syncTimer);
      this.syncTimer = null;
    }
    if (!this.token || !this.repo) return;
    if (this.flushing) {
      this.reflushRequested = true;
      return this.flushing;
    }
    if (!Object.keys(this.pending).length && !this.pendingConfig) {
      if (this.syncStatus.state === "pending") this.setStatus("idle");
      return;
    }

    this.flushing = this.runFlush();
    try {
      await this.flushing;
    } finally {
      this.flushing = null;
      if (this.reflushRequested) {
        this.reflushRequested = false;
        if (Object.keys(this.pending).length || this.pendingConfig) {
          void this.flush();
        }
      }
    }
  }

  private async runFlush(): Promise<void> {
    this.setStatus("saving");
    try {
      if (Object.keys(this.pending).length) await this.flushUserState();
      if (this.pendingConfig) await this.flushConfig();
      this.setStatus("saved");
      setTimeout(() => {
        if (this.syncStatus.state === "saved") this.setStatus("idle");
      }, 2500);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      this.setStatus("error", msg);
    }
  }

  private async flushUserState(): Promise<void> {
    if (!this.repo || !this.token) return;

    const snapshot: Record<string, UserStateItem> = { ...this.pending };

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      const payload: UserState = {
        updated_at: nowIso(),
        items: { ...this.userState.items },
      };

      try {
        this.userStateSha = await writeJson(
          this.repo,
          "user_state.json",
          payload,
          this.token,
          this.userStateSha,
          `chore: user state (${Object.keys(snapshot).length} changes) [skip ci]`,
        );
        this.dropSyncedPending(snapshot);
        return;
      } catch (e) {
        if (!(e instanceof ConflictError) || attempt === MAX_RETRIES - 1) throw e;

        // 409: перечитываем свежий файл, мержим локальные правки по updated_at
        const fresh = await readJson<UserState>(this.repo, "user_state.json", this.token);
        if (fresh) {
          this.userStateSha = fresh.sha;
          for (const [id, entry] of Object.entries(fresh.data.items ?? {})) {
            const local = this.userState.items[id];
            if (!local || (local.updated_at ?? "") < (entry.updated_at ?? "")) {
              this.userState.items[id] = entry;
            }
          }
        } else {
          this.userStateSha = "";
        }
        for (const [id, entry] of Object.entries(snapshot)) {
          this.userState.items[id] = entry;
        }
        await new Promise((r) => setTimeout(r, 250 * (attempt + 1)));
      }
    }
  }

  private dropSyncedPending(snapshot: Record<string, UserStateItem>): void {
    for (const [id, entry] of Object.entries(snapshot)) {
      if (this.pending[id]?.updated_at === entry.updated_at) delete this.pending[id];
    }
    savePending(this.pending);
    this.persistCache();
  }

  private async flushConfig(): Promise<void> {
    if (!this.repo || !this.token || !this.pendingConfig) return;
    const payload = this.pendingConfig;

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      try {
        this.configSha = await writeJson(
          this.repo,
          "config.json",
          payload,
          this.token,
          this.configSha,
          "chore: config update [skip ci]",
        );
        this.pendingConfig = null;
        return;
      } catch (e) {
        if (!(e instanceof ConflictError) || attempt === MAX_RETRIES - 1) throw e;
        const fresh = await readJson<DataConfig>(this.repo, "config.json", this.token);
        if (fresh) {
          this.configSha = fresh.sha;
          // Сохраняем локально изменённые секции, остальное берём с сервера
          this.pendingConfig = {
            ...fresh.data,
            sources: payload.sources,
            settings: { ...fresh.data.settings, ...payload.settings },
            updated_at: nowIso(),
          };
          this.config = this.pendingConfig;
        } else {
          this.configSha = "";
        }
        await new Promise((r) => setTimeout(r, 250 * (attempt + 1)));
      }
    }
  }

  private persistCache(): void {
    saveCache({
      feed: this.feed,
      userState: this.userState,
      config: this.config,
      runs: this.runs,
      fetchedAt: nowIso(),
    });
  }
}

export const store = new FeedStore();
