import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, UnauthorizedError, type FeedItem, type PipelineRun, type Source } from "./api/client";
import { useKeyboardNav, type KeyAction } from "./hooks/useKeyboardNav";
import LoginScreen from "./components/LoginScreen";
import SidebarNav, { type Tab } from "./components/SidebarNav";
import FeedList from "./components/FeedList";
import DislikeToast, { type DislikeState } from "./components/DislikeToast";
import SourcesManager from "./components/SourcesManager";
import RulesAndProfile from "./components/RulesAndProfile";
import PipelineLogs from "./components/PipelineLogs";

const TAB_LABEL: Record<Tab, string> = {
  inbox: "Новое",
  saved: "Отложено",
  archived: "Архив",
  rejected: "Мусор ИИ",
  settings: "Настройки",
};

const SETTINGS_TABS = ["Источники", "Правила и ИИ-профиль", "Логи запусков"] as const;
type SettingsTab = (typeof SETTINGS_TABS)[number];

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  const [tab, setTab] = useState<Tab>("inbox");
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("Источники");

  const [items, setItems] = useState<FeedItem[]>([]);
  const [counts, setCounts] = useState({ inbox: 0, saved: 0, archived: 0, rejected: 0 });
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const [sort, setSort] = useState<"score" | "date">("score");
  const [category, setCategory] = useState("Все");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");

  const [highlightedId, setHighlightedId] = useState<number | null>(null);
  const [dislike, setDislike] = useState<DislikeState | null>(null);

  const [sources, setSources] = useState<Source[]>([]);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [runs, setRuns] = useState<PipelineRun[]>([]);

  const itemsRef = useRef(items);
  itemsRef.current = items;

  // --- bootstrap auth ---
  useEffect(() => {
    api.me().then((ok) => setAuthed(ok));
  }, []);

  // --- debounce search ---
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 350);
    return () => clearTimeout(t);
  }, [query]);

  const handleAuthError = useCallback(() => setAuthed(false), []);

  // --- feed load ---
  const loadFeed = useCallback(async () => {
    if (tab === "settings") return;
    setLoading(true);
    try {
      const res = await api.feed({
        status: tab,
        sort,
        category,
        q: debouncedQuery,
        limit: 60,
      });
      setItems(res.items);
      setCounts(res.counts);
      setCategories(res.categories);
    } catch (e) {
      if (e instanceof UnauthorizedError) handleAuthError();
    } finally {
      setLoading(false);
    }
  }, [tab, sort, category, debouncedQuery, handleAuthError]);

  useEffect(() => {
    if (authed) void loadFeed();
  }, [authed, loadFeed]);

  // --- settings data ---
  const loadSettingsData = useCallback(async () => {
    try {
      const [s, st, r] = await Promise.all([api.sources(), api.settings(), api.runs()]);
      setSources(s.sources);
      setSettings(st.settings);
      setRuns(r.runs);
    } catch (e) {
      if (e instanceof UnauthorizedError) handleAuthError();
    }
  }, [handleAuthError]);

  useEffect(() => {
    if (authed && tab === "settings") void loadSettingsData();
  }, [authed, tab, loadSettingsData]);

  // --- actions ---
  const patchItem = async (
    id: number,
    patch: { status?: string; feedback?: string; feedback_reason?: string | null },
    optimistic?: (it: FeedItem) => FeedItem | null,
  ) => {
    const before = itemsRef.current;
    if (optimistic) {
      setItems((prev) =>
        prev
          .map((it) => (it.id === id ? optimistic(it) : it))
          .filter((it): it is FeedItem => it !== null),
      );
    }
    try {
      await api.patchItem(id, patch);
      void loadFeed();
    } catch (e) {
      setItems(before);
      if (e instanceof UnauthorizedError) handleAuthError();
    }
  };

  const handleItemAction = (item: FeedItem, action: string) => {
    switch (action) {
      case "archive":
        void patchItem(item.id, { status: "archived" });
        break;
      case "save":
        void patchItem(item.id, { status: item.status === "saved" ? "inbox" : "saved" });
        break;
      case "like":
        void patchItem(item.id, {
          feedback: item.feedback === "like" ? "none" : "like",
        });
        break;
      case "restore":
        void patchItem(item.id, { status: "inbox", feedback: "like" });
        break;
      case "dislike": {
        setDislike({ itemId: item.id, title: item.title_ru || item.title_orig });
        void patchItem(item.id, { feedback: "dislike", status: "archived" }, () => null);
        break;
      }
      case "open":
        window.open(item.url, "_blank", "noopener,noreferrer");
        break;
    }
  };

  const handleDislikeReason = (reason: string) => {
    if (!dislike) return;
    void api.patchItem(dislike.itemId, { feedback: "dislike", feedback_reason: reason }).catch(() => undefined);
    setDislike(null);
  };

  const handleUndoDislike = () => {
    if (!dislike) return;
    void patchItem(dislike.itemId, { status: "inbox", feedback: "none" });
    setDislike(null);
  };

  const archiveAll = async () => {
    const ids = items.map((i) => i.id);
    if (!ids.length) return;
    await api.bulk("archive_ids", { ids });
    void loadFeed();
  };

  const archiveOlder = async () => {
    await api.bulk("archive_older_than", { days: 3 });
    void loadFeed();
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
    authed === true && tab !== "settings",
  );

  const logout = async () => {
    await api.logout().catch(() => undefined);
    setAuthed(false);
    setItems([]);
  };

  if (authed === null) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-600" />
      </div>
    );
  }

  if (!authed) {
    return <LoginScreen onSuccess={() => setAuthed(true)} />;
  }

  return (
    <div className="flex h-screen overflow-hidden bg-ink-950">
      <SidebarNav tab={tab} counts={counts} onChange={setTab} onLogout={logout} />

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
            onSortChange={setSort}
            onCategoryChange={setCategory}
            onQueryChange={setQuery}
            onAction={handleItemAction}
            onArchiveAll={archiveAll}
            onArchiveOlder={archiveOlder}
            onRefresh={loadFeed}
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
                {settingsTab === "Логи запусков" && <PipelineLogs runs={runs} />}
              </div>
            </div>
          </div>
        )}
      </main>

      <DislikeToast
        state={dislike}
        onReason={handleDislikeReason}
        onUndo={handleUndoDislike}
        onClose={() => setDislike(null)}
      />
    </div>
  );
}
