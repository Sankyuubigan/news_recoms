import { useState } from "react";
import { Loader2, Plus, Rss, Send, Trash2, Youtube, AlertCircle, Power } from "lucide-react";
import { api, type Source } from "../api/client";
import { relativeTime } from "../utils";

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
  const [identifier, setIdentifier] = useState("");
  const [name, setName] = useState("");
  const [fullText, setFullText] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!identifier.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.createSource({
        type,
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
      setError(/409/.test(msg) ? "Такой источник уже добавлен" : "Не удалось добавить источник");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (s: Source) => {
    await api.patchSource(s.id, { enabled: !s.enabled });
    onReload();
  };

  const toggleFullText = async (s: Source) => {
    await api.patchSource(s.id, { fetch_full_text: !s.fetch_full_text });
    onReload();
  };

  const remove = async (s: Source) => {
    if (!confirm(`Удалить источник «${s.name}» и все его посты?`)) return;
    await api.deleteSource(s.id);
    onReload();
  };

  const meta = TYPE_META[type];

  return (
    <div className="space-y-5">
      <form onSubmit={add} className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <h3 className="mb-3 text-[14px] font-semibold text-white">Добавить источник</h3>

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
          return (
            <div
              key={s.id}
              className="flex items-center gap-3 rounded-lg border border-ink-800 bg-ink-850 px-3 py-2.5"
            >
              <div
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
                  s.enabled ? "bg-ink-800 text-emerald-400" : "bg-ink-800 text-slate-600"
                }`}
              >
                <Icon className="h-4 w-4" />
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className={`truncate text-[13.5px] font-medium ${s.enabled ? "text-slate-200" : "text-slate-500"}`}>
                    {s.name}
                  </span>
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

              {s.type === "rss" && (
                <button
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
                onClick={() => toggle(s)}
                className={`shrink-0 rounded p-1.5 transition ${
                  s.enabled ? "text-emerald-400 hover:bg-ink-800" : "text-slate-600 hover:bg-ink-800"
                }`}
                title={s.enabled ? "Выключить" : "Включить"}
              >
                <Power className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => remove(s)}
                className="shrink-0 rounded p-1.5 text-slate-600 transition hover:bg-ink-800 hover:text-rose-400"
                title="Удалить"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
