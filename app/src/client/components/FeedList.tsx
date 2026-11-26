import { useEffect, useRef } from "react";
import { Archive, ArrowDownWideNarrow, Flame, Loader2, RefreshCw, Search, Sparkles, Trash2 } from "lucide-react";
import type { FeedItem } from "../api/client";
import ItemCard, { type ItemAction } from "./ItemCard";

interface Props {
  items: FeedItem[];
  loading: boolean;
  sort: "score" | "date";
  category: string;
  categories: string[];
  query: string;
  tabLabel: string;
  /** На вкладке мусора набор кнопок другой: там не «просмотрено», а «очистить». */
  isRejectedTab: boolean;
  highlightedId: string | null;
  /** Сколько постов собрано, но ещё не прогнано через LLM. */
  pendingAi: number;
  onSortChange: (s: "score" | "date") => void;
  onCategoryChange: (c: string) => void;
  onQueryChange: (q: string) => void;
  onAction: (item: FeedItem, action: ItemAction) => void;
  onArchiveAll: () => void;
  onArchiveOlder: () => void;
  /** Есть только на вкладке «Мусор ИИ»: убрать всё отсечённое из виду. */
  onClearRejected: () => void;
  onRefresh: () => void;
}

export default function FeedList({
  items,
  loading,
  sort,
  category,
  categories,
  query,
  tabLabel,
  isRejectedTab,
  highlightedId,
  pendingAi,
  onSortChange,
  onCategoryChange,
  onQueryChange,
  onAction,
  onArchiveAll,
  onArchiveOlder,
  onClearRejected,
  onRefresh,
}: Props) {
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  useEffect(() => {
    if (highlightedId == null) return;
    const el = cardRefs.current.get(highlightedId);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [highlightedId]);

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <header className="sticky top-0 z-20 border-b border-ink-800 bg-ink-950/85 px-4 py-3 backdrop-blur">
        <div className="mb-2.5 flex items-center gap-2">
          <h1 className="text-[16px] font-semibold tracking-tight text-white">{tabLabel}</h1>
          <span className="rounded-md bg-ink-800 px-1.5 py-0.5 text-[11px] tabular-nums text-slate-400">
            {items.length}
          </span>

          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={onRefresh}
              className="rounded-lg p-2 text-slate-500 transition hover:bg-ink-850 hover:text-slate-300"
              title="Обновить"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
            <button
              onClick={() => onSortChange(sort === "score" ? "date" : "score")}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-[12px] text-slate-400 transition hover:bg-ink-850 hover:text-slate-200"
              title="Переключить сортировку"
            >
              {sort === "score" ? <Sparkles className="h-3.5 w-3.5" /> : <Flame className="h-3.5 w-3.5" />}
              <span className="hidden sm:inline">{sort === "score" ? "По рейтингу" : "Сначала свежие"}</span>
            </button>
            <button
              onClick={onArchiveOlder}
              className="hidden items-center gap-1.5 rounded-lg px-2.5 py-2 text-[12px] text-slate-400 transition hover:bg-ink-850 hover:text-slate-200 sm:flex"
              title="Смахнуть всё старше 3 дней в архив"
            >
              <ArrowDownWideNarrow className="h-3.5 w-3.5" />
              <span className="hidden md:inline">Старше 3 дней</span>
            </button>
            {isRejectedTab ? (
              <button
                onClick={onClearRejected}
                className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-[12px] text-slate-400 transition hover:bg-rose-500/10 hover:text-rose-300"
                title="Убрать весь мусор из виду (пайплайн сам удалит его через 7 дней)"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span className="hidden md:inline">Очистить мусор</span>
              </button>
            ) : (
              <button
                onClick={onArchiveAll}
                className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-[12px] text-slate-400 transition hover:bg-ink-850 hover:text-slate-200"
                title="Отметить все показанные просмотренными"
              >
                <Archive className="h-3.5 w-3.5" />
                <span className="hidden md:inline">Всё просмотрено</span>
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="relative flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <input
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder="Поиск…"
              className="w-full rounded-lg border border-ink-800 bg-ink-850 py-1.5 pl-8 pr-3 text-[12.5px] text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-emerald-500/50"
            />
          </div>
        </div>

        {categories.length > 0 && (
          <div className="no-scrollbar mt-2.5 flex gap-1.5 overflow-x-auto">
            {["Все", ...categories].map((c) => (
              <button
                key={c}
                onClick={() => onCategoryChange(c)}
                className={`shrink-0 rounded-full px-2.5 py-1 text-[11.5px] transition ${
                  category === c
                    ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
                    : "bg-ink-850 text-slate-400 hover:bg-ink-800 hover:text-slate-200"
                }`}
              >
                {c}
              </button>
            ))}
          </div>
        )}
      </header>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-4 pb-24 pt-3 md:pb-6">
        {loading && items.length === 0 && (
          <div className="flex items-center justify-center py-20 text-slate-500">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        )}

        {!loading && items.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-ink-850">
              <Flame className="h-5 w-5 text-slate-600" />
            </div>
            {pendingAi > 0 ? (
              <>
                <p className="text-[14px] text-slate-300">
                  Собрано {pendingAi} {pendingAi % 10 === 1 && pendingAi % 100 !== 11 ? "пост" : "постов"}, но их ещё не оценил ИИ
                </p>
                <p className="mt-1 max-w-sm text-[12.5px] leading-relaxed text-slate-500">
                  Добавь в репозиторий секрет <code className="text-slate-400">LLM_PROVIDERS_JSON</code> и
                  запусти сбор ещё раз — посты дообработаются и появятся здесь.
                </p>
              </>
            ) : (
              <>
                <p className="text-[14px] text-slate-400">Здесь пока пусто</p>
                <p className="mt-1 max-w-xs text-[12.5px] text-slate-600">
                  Добавь источники в настройках и запусти пайплайн — лента наполнится.
                </p>
              </>
            )}
          </div>
        )}

        <div className="space-y-2.5">
          {items.map((item) => (
            <div
              key={item.id}
              ref={(el) => {
                if (el) cardRefs.current.set(item.id, el);
                else cardRefs.current.delete(item.id);
              }}
            >
              <ItemCard
                item={item}
                highlighted={highlightedId === item.id}
                onAction={(a) => onAction(item, a)}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
