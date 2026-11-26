import { Check, ExternalLink, Plus, ThumbsDown, ThumbsUp, Bookmark, Trash2 } from "lucide-react";
import type { FeedItem } from "../api/client";
import { isEnglish, relativeTime, scoreStyle, sourceLabel, sourceTint } from "../utils";
import { useSwipe } from "../hooks/useSwipe";

export type ItemAction =
  | "archive"
  | "save"
  | "like"
  | "dislike"
  | "open"
  | "restore"
  /** Убрать из «Мусора ИИ», не спасая в ленту: ИИ был прав, просто чистим вкладку. */
  | "dismiss";

interface Props {
  item: FeedItem;
  highlighted: boolean;
  onAction: (action: ItemAction) => void;
}

export default function ItemCard({ item, highlighted, onAction }: Props) {
  const score = scoreStyle(item.ai_score);
  const swipe = useSwipe({
    onSwipeRight: () => onAction("archive"),
    onSwipeLeft: () => onAction("dislike"),
  });

  const inInbox = item.status === "inbox";
  const isSaved = item.status === "saved";
  const isRejected = item.status === "rejected";
  void inInbox;

  return (
    <div className="relative">
      {/* Индикаторы свайпа */}
      {swipe.offsetX > 20 && (
        <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-6 text-emerald-400">
          <Check className="h-6 w-6" />
        </div>
      )}
      {swipe.offsetX < -20 && (
        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-6 text-rose-400">
          <ThumbsDown className="h-6 w-6" />
        </div>
      )}

      <article
        {...swipe.bind}
        style={swipe.style}
        className={`card-hover rounded-xl border bg-ink-850 p-4 ${
          highlighted
            ? "border-emerald-500/50 ring-1 ring-emerald-500/20"
            : "border-ink-800 hover:border-ink-700"
        }`}
      >
        {/* Header */}
        <div className="mb-2.5 flex flex-wrap items-center gap-2">
          <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-semibold tabular-nums ${score.badge}`}>
            {score.label}
          </span>
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold tracking-wide ${sourceTint(item.source_type)}`}>
            {sourceLabel(item.source_type)}
          </span>
          <span className="truncate text-[12.5px] text-slate-400">{item.source_name}</span>
          <span className="text-[12px] text-slate-600">·</span>
          <span className="text-[12px] text-slate-500">{relativeTime(item.published_at)}</span>
          {isEnglish(item) && (
            <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-violet-300">
              EN → RU
            </span>
          )}
          {item.category && item.category !== "Общее" && (
            <span className="rounded bg-ink-700/70 px-1.5 py-0.5 text-[10.5px] text-slate-400">
              {item.category}
            </span>
          )}
          {isRejected && (
            <span className="ml-auto rounded bg-rose-500/10 px-1.5 py-0.5 text-[10.5px] text-rose-300/90">
              отсечено
            </span>
          )}
        </div>

        {/* Title */}
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className="group block text-[15.5px] font-semibold leading-snug text-slate-100 hover:text-emerald-300"
        >
          {item.title_ru || item.title_orig}
        </a>

        {/* Cover */}
        {item.media_url && (
          <a href={item.url} target="_blank" rel="noopener noreferrer" className="mt-3 block">
            <img
              src={item.media_url}
              alt=""
              loading="lazy"
              className="max-h-64 w-full rounded-lg object-cover"
              onError={(e) => {
                (e.currentTarget as HTMLImageElement).style.display = "none";
              }}
            />
          </a>
        )}

        {/* Summary */}
        {item.summary_ru && (
          <p className="mt-2.5 text-[13.5px] leading-relaxed text-slate-300 line-clamp-4">{item.summary_ru}</p>
        )}

        {/* Key points */}
        {item.key_points.length > 0 && (
          <ul className="mt-2.5 space-y-1">
            {item.key_points.slice(0, 4).map((kp, i) => (
              <li key={i} className="flex gap-2 text-[13px] leading-relaxed text-slate-400">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-600" />
                <span>{kp}</span>
              </li>
            ))}
          </ul>
        )}

        {/* Merged sources */}
        {item.merged_sources.length > 0 && (
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-[12px] text-slate-500">
            <span>Также об этом писали (+{item.merged_sources.length}):</span>
            {item.merged_sources.slice(0, 4).map((m, i) => (
              <a
                key={i}
                href={m.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-slate-400 underline decoration-slate-700 underline-offset-2 hover:text-emerald-300"
              >
                {m.name}
              </a>
            ))}
          </div>
        )}

        {/* AI reason */}
        {(item.ai_reason || item.ad_reason) && (
          <p className="mt-2.5 border-l-2 border-ink-600 pl-2.5 text-[12px] italic leading-relaxed text-slate-500">
            {item.ad_reason ?? item.ai_reason}
          </p>
        )}

        {/* Tags */}
        {item.tags.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {item.tags.slice(0, 6).map((t) => (
              <span key={t} className="rounded bg-ink-800 px-1.5 py-0.5 text-[11px] text-slate-500">
                #{t}
              </span>
            ))}
          </div>
        )}

        {/* Actions */}
        <div className="mt-3.5 flex items-center gap-1.5 border-t border-ink-800 pt-3">
          {isRejected ? (
            <>
              {/* Главное действие вкладки — спасти ложно отсечённое. */}
              <button
                onClick={() => onAction("restore")}
                className="flex items-center gap-1.5 rounded-lg bg-emerald-500/10 px-3 py-2 text-[12.5px] font-medium text-emerald-300 transition hover:bg-emerald-500/20"
                title="Спасти пост обратно в ленту и обучить ИИ"
              >
                <ThumbsUp className="h-4 w-4" />
                В ленту
              </button>
              {/* ИИ был прав — просто убираем из вкладки, не трогая обучение. */}
              <button
                onClick={() => onAction("dismiss")}
                className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-[12.5px] text-slate-400 transition hover:bg-ink-800 hover:text-slate-200"
                title="Убрать из мусора, не возвращая в ленту"
              >
                <Trash2 className="h-4 w-4" />
                <span className="hidden sm:inline">Убрать</span>
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => onAction("archive")}
                className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-[12.5px] text-slate-400 transition hover:bg-ink-800 hover:text-slate-200"
                title="Просмотрено (e)"
              >
                <Check className="h-4 w-4" />
                <span className="hidden sm:inline">Просмотрено</span>
              </button>
              <button
                onClick={() => onAction("save")}
                className={`flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-[12.5px] transition hover:bg-ink-800 ${
                  isSaved ? "text-amber-300" : "text-slate-400 hover:text-slate-200"
                }`}
                title="Отложить (s)"
              >
                <Bookmark className={`h-4 w-4 ${isSaved ? "fill-amber-300" : ""}`} />
                <span className="hidden sm:inline">{isSaved ? "Отложено" : "Отложить"}</span>
              </button>
            </>
          )}

          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => onAction("like")}
              className={`rounded-lg p-2 transition hover:bg-ink-800 ${
                item.feedback === "like" ? "text-emerald-400" : "text-slate-500 hover:text-emerald-400"
              }`}
              title="Нравится (l)"
            >
              <ThumbsUp className={`h-4 w-4 ${item.feedback === "like" ? "fill-emerald-400" : ""}`} />
            </button>
            <button
              onClick={() => onAction("dislike")}
              className={`rounded-lg p-2 transition hover:bg-ink-800 ${
                item.feedback === "dislike" ? "text-rose-400" : "text-slate-500 hover:text-rose-400"
              }`}
              title="Не нравится (d)"
            >
              <ThumbsDown className={`h-4 w-4 ${item.feedback === "dislike" ? "fill-rose-400" : ""}`} />
            </button>
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg p-2 text-slate-500 transition hover:bg-ink-800 hover:text-slate-300"
              title="Открыть оригинал (o)"
            >
              <ExternalLink className="h-4 w-4" />
            </a>
          </div>
        </div>
      </article>
    </div>
  );
}
