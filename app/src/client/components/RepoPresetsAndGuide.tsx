import { useState } from "react";
import { Check, ChevronDown, FileJson, Loader2, Package, Plus } from "lucide-react";
import { api, type ContentKind, type Source, type SourceType } from "../api/client";

export interface SourcePreset {
  type: SourceType;
  kind: ContentKind;
  name: string;
  identifier: string;
  category_hint: string;
  description: string;
}

export const REPO_PRESETS: SourcePreset[] = [
  {
    type: "rss",
    kind: "repos",
    name: "GitHub Trending (Daily)",
    identifier: "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml",
    category_hint: "Open Source",
    description: "Ежедневные тренды GitHub по всем языкам",
  },
  {
    type: "rss",
    kind: "repos",
    name: "Show HN (50+)",
    identifier: "https://hnrss.org/show?points=50",
    category_hint: "Инструменты",
    description: "Новые проекты и тулзы с Hacker News (от 50 баллов)",
  },
  {
    type: "rss",
    kind: "repos",
    name: "selfh.st",
    identifier: "https://selfh.st/rss/",
    category_hint: "Self-hosted",
    description: "Дайджесты и релизы практичных self-hosted приложений",
  },
  {
    type: "rss",
    kind: "repos",
    name: "Console.dev",
    identifier: "https://console.dev/rss.xml",
    category_hint: "DevTools",
    description: "Курируемые обзоры лучших инструментов для разработчиков",
  },
  {
    type: "rss",
    kind: "repos",
    name: "Lobsters: Show",
    identifier: "https://lobste.rs/t/show.rss",
    category_hint: "Open Source",
    description: "Технические open-source проекты сообщества Lobsters",
  },
  {
    type: "telegram",
    kind: "repos",
    name: "Находки в опенсорсе",
    identifier: "opensource_findings",
    category_hint: "Open Source",
    description: "Русскоязычный канал с подборками open-source проектов",
  },
];

interface Props {
  sources: Source[];
  onReload: () => void;
}

export default function RepoPresetsAndGuide({ sources, onReload }: Props) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);

  const existing = new Set(sources.map((s) => s.identifier));
  const missing = REPO_PRESETS.filter((p) => !existing.has(p.identifier));

  const addOne = async (preset: SourcePreset) => {
    setBusyId(preset.identifier);
    try {
      await api.createSource({
        type: preset.type,
        kind: preset.kind,
        identifier: preset.identifier,
        name: preset.name,
        category_hint: preset.category_hint,
      });
      onReload();
    } finally {
      setBusyId(null);
    }
  };

  const addAll = async () => {
    setBusyId("__all__");
    try {
      for (const preset of missing) {
        await api.createSource({
          type: preset.type,
          kind: preset.kind,
          identifier: preset.identifier,
          name: preset.name,
          category_hint: preset.category_hint,
        });
      }
      onReload();
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Package className="h-4 w-4 text-emerald-400" />
            <h3 className="text-[13.5px] font-semibold text-white">Топ-источники Open Source</h3>
          </div>
          {missing.length > 0 && (
            <button
              type="button"
              onClick={addAll}
              disabled={busyId !== null}
              className="flex items-center gap-1 rounded-lg bg-emerald-500/15 px-2.5 py-1.5 text-[11.5px] font-medium text-emerald-300 transition hover:bg-emerald-500/25 disabled:opacity-40"
            >
              {busyId === "__all__" ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Plus className="h-3 w-3" />
              )}
              Добавить все (+{missing.length})
            </button>
          )}
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          {REPO_PRESETS.map((p) => {
            const added = existing.has(p.identifier);
            return (
              <div
                key={p.identifier}
                className="flex items-center gap-2.5 rounded-lg border border-ink-800 bg-ink-900/70 px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium text-slate-200">{p.name}</div>
                  <div className="truncate text-[11px] text-slate-500">{p.description}</div>
                </div>
                {added ? (
                  <span className="flex shrink-0 items-center gap-1 rounded-md bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">
                    <Check className="h-3 w-3" />
                    Добавлен
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => addOne(p)}
                    disabled={busyId !== null}
                    className="shrink-0 rounded-md bg-ink-800 px-2 py-1 text-[11.5px] text-slate-300 transition hover:bg-ink-700 hover:text-white disabled:opacity-40"
                  >
                    {busyId === p.identifier ? <Loader2 className="h-3 w-3 animate-spin" /> : "Добавить"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <button
          type="button"
          onClick={() => setGuideOpen((v) => !v)}
          className="flex w-full items-center gap-2 text-left"
        >
          <FileJson className="h-4 w-4 shrink-0 text-slate-400" />
          <span className="flex-1 text-[13px] font-medium text-slate-200">
            Запасной способ разбора архива: экспорт из Telegram Desktop (JSON)
          </span>
          <ChevronDown
            className={`h-4 w-4 text-slate-500 transition ${guideOpen ? "rotate-180" : ""}`}
          />
        </button>

        {guideOpen && (
          <div className="mt-3 space-y-2 border-t border-ink-800 pt-3 text-[12px] leading-relaxed text-slate-400">
            <p>
              Если канал приватный или веб-пагинация <code className="text-slate-300">t.me/s/</code>{" "}
              ограничивает глубину истории, можно выгрузить посты из Telegram Desktop и залить их в
              очередь ИИ локально:
            </p>
            <ol className="list-decimal space-y-1 pl-4 text-slate-400">
              <li>
                Открой нужный канал в <b className="text-slate-300">Telegram Desktop</b> → меню{" "}
                <code className="text-slate-300">⋮</code> → <b className="text-slate-300">Экспорт истории чата</b>.
              </li>
              <li>
                Сними все галочки с медиа (фото, видео, файлы не нужны), выбери формат{" "}
                <b className="text-slate-300">Машиночитаемый JSON</b> и нужный период → получишь файл{" "}
                <code className="text-slate-300">result.json</code>.
              </li>
              <li>
                Склонируй ветку данных рядом с проектом и прогони скрипт импорта:
                <pre className="mt-1.5 overflow-x-auto rounded-lg bg-ink-950 p-2.5 font-mono text-[11px] text-slate-300">
{`git clone --branch data --single-branch https://github.com/<ник>/<репо>.git data_repo
cd pipeline
python import_tg_export.py --export /путь/к/result.json --data-dir ../data_repo --handle <канал> --kind repos
cd ../data_repo && git add feed.json && git commit -m "chore: import archive" && git push origin data`}
                </pre>
              </li>
              <li>
                Дальше регулярный крон GitHub Actions сам порциями оценит все импортированные посты
                через ИИ и разложит годные в вкладку «Репозитории».
              </li>
            </ol>
          </div>
        )}
      </section>
    </div>
  );
}