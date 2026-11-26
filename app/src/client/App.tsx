import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, store, type FeedItem, type PipelineRun, type Source, type SyncStatus } from "./api/client";
import { getToken, setDataRepoOverride, setToken } from "./api/github";
import { useKeyboardNav, type KeyAction } from "./hooks/useKeyboardNav";
import SidebarNav, { type Tab } from "./components/SidebarNav";
import FeedList from "./components/FeedList";
import FeedbackToast, { type FeedbackState } from "./components/FeedbackToast";
import SourcesManager from "./components/SourcesManager";
import RulesAndProfile from "./components/RulesAndProfile";
import PipelineLogs from "./components/PipelineLogs";
import SyncBanner from "./components/SyncBanner";
import SyncModal from "./components/SyncModal";
import SyncSettings from "./components/SyncSettings";

const TAB_LABEL: Record<Tab, string> = {
  inbox: "Новое",
  saved: "Отложено",
  archived: "Архив",
  rejected: "Мусор ИИ",
  settings: "Настройки",
};

const SETTINGS_TABS = ["Источники", "Правила и ИИ-профиль", "Синхронизация", "Логи запусков"] as const;
type SettingsTab = (typeof SETTINGS_TABS)[number];

const HASH_SYNC = "#sync_token=";

/** Разбирает `#sync_token=...&repo=owner/repo` с телефона. */
function consumeSyncHash(): { connected: boolean } {
  const hash = window.location.hash;
  if (!hash.startsWith(HASH_SYNC)) return { connected: false };

  const params = new URLSearchParams(hash.slice(1));
  const token = params.get("sync_token");
  const repo = params.get("repo");

  if (token) setToken(token);
  if (repo) setDataRepoOverride(repo);

  // Мгновенно убираем токен из адресной строки
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return { connected: Boolean(token) };
}

export default function App() {
  const [ready, setReady] = useState(false);
  const [sync, setSync] = useState<SyncStatus>({ state: "idle" });
  const [toast, setToast] = useState<string | null>(null);

  const [tab, setTab] = useState<Tab>("inbox");
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("Источники");

  const [items, setItems] = useState<FeedItem[]>([]);
  const [counts, setCounts] = useState({
    inbox: 0,
    saved: 0,
    archived: 0,
    rejected: 0,
    pending_ai: 0,
  });
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const [sort, setSort] = useState<"score" | "date">("score");
  const [category, setCategory] = useState("Все");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");

  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<FeedbackState | null>(null);

  const [sources, setSources] = useState<Source[]>([]);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [runs, setRuns] = useState<PipelineRun[]>([]);

  const [syncModalOpen, setSyncModalOpen] = useState(false);

  const itemsRef = useRef(items);
  itemsRef.current = items;

  // --- подключение по QR-ссылке + первичная загрузка ---
  useEffect(() => {
    const { connected } = consumeSyncHash();
    if (connected) setToast("Телефон подключен! Синхронизация активна");
    void store.load().finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  // --- подписка на статус синхронизации ---
  useEffect(() => {
    const update = () => setSync(store.getStatus());
    update();
    return store.subscribe(update);
  }, []);

  // --- debounce search ---
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 350);
    return () => clearTimeout(t);
  }, [query]);

  // --- feed load ---
  const loadFeed = useCallback(async () => {
    if (tab === "settings") return;
    setLoading(true);
    try {
      const res = await api.feed({ status: tab, sort, category, q: debouncedQuery, limit: 60 });
      setItems(res.items);
      setCounts(res.counts);
      setCategories(res.categories);
    } finally {
      setLoading(false);
    }
  }, [tab, sort, category, debouncedQuery]);

  useEffect(() => {
    if (ready) void loadFeed();
  }, [ready, loadFeed]);

  // --- settings data ---
  const loadSettingsData = useCallback(async () => {
    const [s, st, r] = await Promise.all([api.sources(), api.settings(), api.runs()]);
    setSources(s.sources);
    setSettings(st.settings);
    setRuns(r.runs);
  }, []);

  useEffect(() => {
    if (ready && tab === "settings") void loadSettingsData();
  }, [ready, tab, loadSettingsData]);

  // --- actions ---
  const patchItem = (
    id: string,
    patch: { status?: string; feedback?: string; feedback_reason?: string | null },
  ) => {
    store.patchItem(id, patch as Parameters<typeof store.patchItem>[1]);
    void loadFeed();
  };

  const handleItemAction = (item: FeedItem, action: string) => {
    switch (action) {
      case "archive":
        patchItem(item.id, { status: "archived" });
        break;
      case "save":
        patchItem(item.id, { status: item.status === "saved" ? "inbox" : "saved" });
        break;
      case "like": {
        // Лайк ставим сразу, а причину — если захочет, в тосте.
        patchItem(item.id, { feedback: item.feedback === "like" ? "none" : "like" });
        if (item.feedback !== "like") {
          setFeedback({ itemId: item.id, title: item.title_ru || item.title_orig, kind: "like" });
        }
        break;
      }
      case "restore":
        patchItem(item.id, { status: "inbox", feedback: "like" });
        break;
      case "dislike": {
        setFeedback({ itemId: item.id, title: item.title_ru || item.title_orig, kind: "dislike" });
        patchItem(item.id, { feedback: "dislike", status: "archived" });
        setItems((prev) => prev.filter((it) => it.id !== item.id));
        break;
      }
      case "open":
        window.open(item.url, "_blank", "noopener,noreferrer");
        break;
    }
  };

  const handleFeedbackReason = (reason: string) => {
    if (!feedback) return;
    const kind = feedback.kind;
    store.patchItem(feedback.itemId, {
      feedback: kind,
      feedback_reason: reason,
      // Дизлайк уже увёл пост в архив, лайк статус не меняет.
      ...(kind === "dislike" ? { status: "archived" as const } : {}),
    });
    setFeedback(null);
  };

  const handleUndoFeedback = () => {
    if (!feedback) return;
    const kind = feedback.kind;
    patchItem(feedback.itemId, {
      feedback: "none",
      feedback_reason: null,
      ...(kind === "dislike" ? { status: "inbox" as const } : {}),
    });
    setFeedback(null);
  };

  const archiveAll = async () => {
    // Берём ID ВСЕЙ вкладки, а не только загруженных 60: иначе в архиве
    // оставались бы сотни непросмотренных, которые просто не попали в экран.
    const ids = await api.feedIds({ status: tab, category, q: debouncedQuery });
    if (!ids.length) return;
    await api.bulk("archive_ids", { ids });
    void loadFeed();
  };

  const archiveOlder = async () => {
    await api.bulk("archive_older_than", { days: 3 });
    void loadFeed();
  };

  const refresh = async () => {
    await api.refresh();
    void loadFeed();
    if (tab === "settings") void loadSettingsData();
  };

  // --- keyboard nav ---
  useKeyboardNav(
    (action: KeyAction) => {
      if (tab === "settings") return;
      const list = itemsRef.current;
      if (!list.length) return;

      const idx = highlightedId == null ? -1 : list.findIndex((i) => i.id === highlightedId);
      const current = idx >= 0 ? list[idx] : null;

      if (action === "next") {
        const next = list[Math.min(idx + 1, list.length - 1)];
        if (next) setHighlightedId(next.id);
        return;
      }
      if (action === "prev") {
        const prev = list[Math.max(idx - 1, 0)];
        if (prev) setHighlightedId(prev.id);
        return;
      }
      if (!current) {
        if (list[0]) setHighlightedId(list[0].id);
        return;
      }

      const map: Record<string, string> = {
        archive: "archive",
        save: "save",
        like: "like",
        dislike: "dislike",
        open: "open",
      };
      const a = map[action];
      if (a) handleItemAction(current, a);
    },
    tab !== "settings",
  );

  const hasToken = useMemo(() => Boolean(getToken()), [sync, syncModalOpen, ready]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-600" />
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-ink-950">
      <SyncBanner
        status={sync}
        onConnect={() => setSyncModalOpen(true)}
        onSyncNow={() => void store.flush()}
      />

      <div className="flex min-h-0 flex-1">
        <SidebarNav
          tab={tab}
          counts={counts}
          onChange={setTab}
          onOpenSync={() => setSyncModalOpen(true)}
          synced={hasToken}
        />

        <main className="flex min-w-0 flex-1 flex-col">
          {tab !== "settings" ? (
            <FeedList
              items={items}
              loading={loading}
              sort={sort}
              category={category}
              categories={categories}
              query={query}
              tabLabel={TAB_LABEL[tab]}
              highlightedId={highlightedId}
              pendingAi={counts.pending_ai}
              onSortChange={setSort}
              onCategoryChange={setCategory}
              onQueryChange={setQuery}
              onAction={handleItemAction}
              onArchiveAll={archiveAll}
              onArchiveOlder={archiveOlder}
              onRefresh={refresh}
            />
          ) : (
            <div className="flex h-full flex-col">
              <header className="border-b border-ink-800 px-4 py-3">
                <h1 className="mb-2.5 text-[16px] font-semibold tracking-tight text-white">Настройки</h1>
                <div className="no-scrollbar flex gap-1.5 overflow-x-auto">
                  {SETTINGS_TABS.map((t) => (
                    <button
                      key={t}
                      onClick={() => setSettingsTab(t)}
                      className={`shrink-0 rounded-lg px-3 py-1.5 text-[12.5px] transition ${
                        settingsTab === t
                          ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
                          : "bg-ink-850 text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </header>

              <div className="flex-1 overflow-y-auto px-4 pb-24 pt-4 md:pb-6">
                <div className="mx-auto max-w-3xl">
                  {settingsTab === "Источники" && (
                    <SourcesManager sources={sources} onReload={loadSettingsData} />
                  )}
                  {settingsTab === "Правила и ИИ-профиль" && (
                    <RulesAndProfile settings={settings} onReload={loadSettingsData} />
                  )}
                  {settingsTab === "Синхронизация" && (
                    <SyncSettings
                      onOpenModal={() => setSyncModalOpen(true)}
                      onRefresh={refresh}
                      onNotify={setToast}
                    />
                  )}
                  {settingsTab === "Логи запусков" && <PipelineLogs runs={runs} />}
                </div>
              </div>
            </div>
          )}
        </main>
      </div>

      <FeedbackToast
        state={feedback}
        onReason={handleFeedbackReason}
        onUndo={handleUndoFeedback}
        onClose={() => setFeedback(null)}
      />

      <SyncModal open={syncModalOpen} onClose={() => setSyncModalOpen(false)} />

      {toast && (
        <div className="pointer-events-none fixed inset-x-0 top-3 z-[60] flex justify-center px-4">
          <div className="pf-slide-up rounded-lg border border-emerald-500/30 bg-ink-850/95 px-3.5 py-2 text-[12.5px] text-emerald-300 shadow-xl backdrop-blur">
            {toast}
          </div>
        </div>
      )}
    </div>
  );
}
