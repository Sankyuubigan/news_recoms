import { useState } from "react";
import {
  AlertCircle,
  Flame,
  History,
  Loader2,
  Package,
  Plus,
  Power,
  Rss,
  Send,
  Square,
  Trash2,
  Youtube,
} from "lucide-react";
import {
  api,
  parseTelegramPostId,
  type BackfillConfig,
  type ContentKind,
  type Source,
} from "../api/client";
import { relativeTime } from "../utils";
import RepoPresetsAndGuide from "./RepoPresetsAndGuide";

interface Props {
  sources: Source[];
  onReload: () => void;
}

const TYPE_META = {
  telegram: { label: "Telegram", icon: Send, placeholder: "@channel или https://t.me/channel" },
  rss: { label: "RSS / Сайт", icon: Rss, placeholder: "https://example.com/feed" },
  youtube: { label: "YouTube", icon: Youtube, placeholder: "UC… / @handle / ссылка на канал" },
} as const;

export default function SourcesManager({ sources, onReload }: Props) {
  const [type, setType] = useState<"telegram" | "rss" | "youtube">("telegram");
  const [kind, setKind] = useState<ContentKind>("news");
  const [identifier, setIdentifier] = useState("");
  const [name, setName] = useState("");
  const [fullText, setFullText] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Состояние открытой панели настройки разбора архива для конкретного источника
  const [backfillOpenId, setBackfillOpenId] = useState<number | null>(null);
  const [bfStopInput, setBfStopInput] = useState("");
  const [bfCountInput, setBfCountInput] = useState("");
  const [bfBatchInput, setBfBatchInput] = useState("80");
  const [bfError, setBfError] = useState<string | null>(null);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!identifier.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.createSource({
        type,
        kind,
        identifier: identifier.trim(),
        name: name.trim(),
        fetch_full_text: fullText,
      });
      setIdentifier("");
      setName("");
      setFullText(false);
      onReload();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(
        /уже добавлен|duplicate/i.test(msg)
          ? "Такой источник уже добавлен"
          : msg || "Не удалось добавить источник",
      );
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (s: Source) => {
    await api.patchSource(s.id, { enabled: !s.enabled });
    onReload();
  };

  const toggleKind = async (s: Source) => {
    const next: ContentKind = s.kind === "repos" ? "news" : "repos";
    await api.patchSource(s.id, { kind: next });
    onReload();
  };

  const toggleFullText = async (s: Source) => {
    await api.patchSource(s.id, { fetch_full_text: !s.fetch_full_text });
    onReload();
  };

  const remove = async (s: Source) => {
    if (!confirm(`Удалить источник «${s.name}» из списка сбора?`)) return;
    await api.deleteSource(s.id);
    onReload();
  };

  const openBackfill = (s: Source) => {
    if (backfillOpenId === s.id) {
      setBackfillOpenId(null);
      return;
    }
    setBackfillOpenId(s.id);
    setBfError(null);
    setBfStopInput(s.backfill?.stop_at_id ? String(s.backfill.stop_at_id) : "");
    setBfCountInput(s.backfill?.remaining ? String(s.backfill.remaining) : "");
    setBfBatchInput(String(s.backfill?.batch_limit ?? 80));
  };

  const startBackfill = async (s: Source) => {
    setBfError(null);
    const stopId = parseTelegramPostId(bfStopInput);
    const remaining = bfCountInput.trim() ? Number(bfCountInput.trim()) : null;
    const batch = Math.max(20, Math.min(250, Number(bfBatchInput) || 80));

    if (bfStopInput.trim() && stopId == null) {
      setBfError("Вставь ссылку вида https://t.me/channel/4120 или числовой ID поста");
      return;
    }
    if (remaining !== null && (!Number.isFinite(remaining) || remaining <= 0)) {
      setBfError("Количество постов должно быть положительным числом");
      return;
    }
    if (stopId == null && remaining == null) {
      setBfError("Укажи ссылку/ID последнего прочитанного поста или количество постов");
      return;
    }

    const backfill: BackfillConfig = {
      enabled: true,
      stop_at_id: stopId,
      remaining,
      cursor_id: null,
      batch_limit: batch,
      processed_total: 0,
    };
    await api.patchSource(s.id, { backfill });
    setBackfillOpenId(null);
    onReload();
  };

  const stopBackfill = async (s: Source) => {
    if (!s.backfill) return;
    await api.patchSource(s.id, { backfill: { ...s.backfill, enabled: false } });
    onReload();
  };

  const meta = TYPE_META[type];

  return (
    <div className="space-y-5">
      <form onSubmit={add} className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[14px] font-semibold text-white">Добавить источник</h3>

          <div className="flex rounded-lg border border-ink-800 bg-ink-900 p-0.5">
            <button
              type="button"
              onClick={() => setKind("news")}
              className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-[11.5px] transition ${
                kind === "news"
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Flame className="h-3 w-3" />
              Новости
            </button>
            <button
              type="button"
              onClick={() => setKind("repos")}
              className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-[11.5px] transition ${
                kind === "repos"
                  ? "bg-cyan-500/15 text-cyan-300"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Package className="h-3 w-3" />
              Репозитории
            </button>
          </div>
        </div>

        <div className="mb-3 flex gap-1.5">
          {(Object.keys(TYPE_META) as Array<keyof typeof TYPE_META>).map((t) => {
            const Icon = TYPE_META[t].icon;
            return (
              <button
                key={t}
                type="button"
                onClick={() => setType(t)}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-[12.5px] transition ${
                  type === t
                    ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
                    : "bg-ink-800 text-slate-400 hover:text-slate-200"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {TYPE_META[t].label}
              </button>
            );
          })}
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <input
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder={meta.placeholder}
            className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-emerald-500/50"
          />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Название (необязательно)"
            className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-emerald-500/50"
          />
        </div>

        {type === "rss" && (
          <label className="mt-2.5 flex cursor-pointer items-center gap-2 text-[12.5px] text-slate-400">
            <input
              type="checkbox"
              checked={fullText}
              onChange={(e) => setFullText(e.target.checked)}
              className="h-3.5 w-3.5 accent-emerald-500"
            />
            Вытягивать полный текст статьи (trafilatura)
          </label>
        )}

        {error && <p className="mt-2 text-[12.5px] text-rose-400">{error}</p>}

        <button
          type="submit"
          disabled={busy || !identifier.trim()}
          className="mt-3 flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3.5 py-2 text-[13px] font-semibold text-ink-950 transition hover:bg-emerald-400 disabled:opacity-40"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          Добавить
        </button>
      </form>

      <div className="space-y-1.5">
        {sources.length === 0 && (
          <p className="py-8 text-center text-[13px] text-slate-500">
            Источников пока нет. Добавь первый Telegram-канал, RSS или YouTube.
          </p>
        )}
        {sources.map((s) => {
          const Icon = TYPE_META[s.type].icon;
          const isRepo = s.kind === "repos";
          const bf = s.backfill;
          const bfActive = Boolean(bf?.enabled);
          const bfOpen = backfillOpenId === s.id;

          return (
            <div key={s.id} className="rounded-lg border border-ink-800 bg-ink-850 px-3 py-2.5">
              <div className="flex items-center gap-3">
                <div
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
                    s.enabled ? "bg-ink-800 text-emerald-400" : "bg-ink-800 text-slate-600"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`truncate text-[13.5px] font-medium ${
                        s.enabled ? "text-slate-200" : "text-slate-500"
                      }`}
                    >
                      {s.name}
                    </span>
                    <button
                      type="button"
                      onClick={() => toggleKind(s)}
                      className={`rounded px-1.5 py-0.5 text-[10px] font-medium transition ${
                        isRepo
                          ? "bg-cyan-500/15 text-cyan-300 hover:bg-cyan-500/25"
                          : "bg-ink-800 text-slate-500 hover:text-slate-300"
                      }`}
                      title="Переключить поток: Новости / Репозитории"
                    >
                      {isRepo ? "Репозитории" : "Новости"}
                    </button>
                    {bfActive && (
                      <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">
                        Архив: {bf?.processed_total ?? 0}
                        {bf?.cursor_id ? ` · #${bf.cursor_id}` : ""}
                      </span>
                    )}
                    {s.last_error && (
                      <span title={s.last_error}>
                        <AlertCircle className="h-3.5 w-3.5 shrink-0 text-rose-400" />
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-[11.5px] text-slate-500">
                    <span className="truncate">{s.identifier}</span>
                    {s.last_fetched_at && <span>· {relativeTime(s.last_fetched_at)}</span>}
                  </div>
                </div>

                {s.type === "telegram" && (
                  <button
                    type="button"
                    onClick={() => openBackfill(s)}
                    className={`shrink-0 rounded p-1.5 transition ${
                      bfActive || bfOpen
                        ? "bg-amber-500/15 text-amber-300"
                        : "text-slate-500 hover:bg-ink-800 hover:text-slate-200"
                    }`}
                    title="Разбор старых постов канала (Backfill)"
                  >
                    <History className="h-3.5 w-3.5" />
                  </button>
                )}

                {s.type === "rss" && (
                  <button
                    type="button"
                    onClick={() => toggleFullText(s)}
                    className={`hidden shrink-0 rounded px-1.5 py-1 text-[10.5px] transition sm:block ${
                      s.fetch_full_text
                        ? "bg-emerald-500/15 text-emerald-300"
                        : "bg-ink-800 text-slate-500 hover:text-slate-300"
                    }`}
                    title="Полный текст статьи"
                  >
                    full
                  </button>
                )}

                <button
                  type="button"
                  onClick={() => toggle(s)}
                  className={`shrink-0 rounded p-1.5 transition ${
                    s.enabled ? "text-emerald-400 hover:bg-ink-800" : "text-slate-600 hover:bg-ink-800"
                  }`}
                  title={s.enabled ? "Выключить" : "Включить"}
                >
                  <Power className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => remove(s)}
                  className="shrink-0 rounded p-1.5 text-slate-600 transition hover:bg-ink-800 hover:text-rose-400"
                  title="Удалить"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>

              {bfOpen && s.type === "telegram" && (
                <div className="mt-3 space-y-2.5 border-t border-ink-800 pt-3">
                  <p className="text-[11.5px] leading-relaxed text-slate-400">
                    Пайплайн будет порциями листать историю канала назад через{" "}
                    <code className="text-slate-300">t.me/s/{s.identifier}?before=…</code> и прогонять
                    посты через ИИ, сохраняя прогресс между запусками.
                  </p>
                  <div className="grid gap-2 sm:grid-cols-3">
                    <input
                      value={bfStopInput}
                      onChange={(e) => setBfStopInput(e.target.value)}
                      placeholder="Ссылка/ID последнего прочитанного"
                      className="rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-[12px] text-slate-200 outline-none placeholder:text-slate-600 focus:border-emerald-500/50 sm:col-span-2"
                    />
                    <input
                      value={bfCountInput}
                      onChange={(e) => setBfCountInput(e.target.value)}
                      placeholder="Или сколько постов"
                      inputMode="numeric"
                      className="rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-[12px] text-slate-200 outline-none placeholder:text-slate-600 focus:border-emerald-500/50"
                    />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="flex items-center gap-1.5 text-[11.5px] text-slate-400">
                      <span>Порция за запуск:</span>
                      <input
                        value={bfBatchInput}
                        onChange={(e) => setBfBatchInput(e.target.value)}
                        inputMode="numeric"
                        className="w-16 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-center text-[12px] text-slate-200 outline-none"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => startBackfill(s)}
                      className="ml-auto rounded-lg bg-emerald-500 px-3 py-1.5 text-[12px] font-semibold text-ink-950 transition hover:bg-emerald-400"
                    >
                      {bfActive ? "Перезапустить разбор" : "Запустить разбор"}
                    </button>
                    {bfActive && (
                      <button
                        type="button"
                        onClick={() => stopBackfill(s)}
                        className="flex items-center gap-1 rounded-lg bg-rose-500/15 px-2.5 py-1.5 text-[12px] text-rose-300 transition hover:bg-rose-500/25"
                      >
                        <Square className="h-3 w-3" />
                        Стоп
                      </button>
                    )}
                  </div>
                  {bfError && <p className="text-[11.5px] text-rose-400">{bfError}</p>}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <RepoPresetsAndGuide sources={sources} onReload={onReload} />
    </div>
  );
}