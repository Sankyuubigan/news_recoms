import { useEffect, useState } from "react";
import { Flame, Loader2, Package, Save } from "lucide-react";
import { api, type ContentKind } from "../api/client";

interface Props {
  settings: Record<string, string>;
  onReload: () => void;
}

export default function RulesAndProfile({ settings, onReload }: Props) {
  const [kind, setKind] = useState<ContentKind>("news");
  const [manualRules, setManualRules] = useState("");
  const [learnedProfile, setLearnedProfile] = useState("");
  const [repoRules, setRepoRules] = useState("");
  const [repoLearnedProfile, setRepoLearnedProfile] = useState("");
  const [adRegex, setAdRegex] = useState("");
  const [minScore, setMinScore] = useState("6.5");
  const [minReposScore, setMinReposScore] = useState("6.5");
  const [alertScore, setAlertScore] = useState("9.0");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setManualRules(settings.manual_rules ?? "");
    setLearnedProfile(settings.learned_profile ?? "");
    setRepoRules(settings.repo_rules ?? "");
    setRepoLearnedProfile(settings.repo_learned_profile ?? "");
    setAdRegex(settings.ad_regex_blacklist ?? "");
    setMinScore(settings.min_inbox_score ?? "6.5");
    setMinReposScore(settings.min_repos_score ?? "6.5");
    setAlertScore(settings.telegram_alert_score ?? "9.0");
  }, [settings]);

  const save = async () => {
    setSaving(true);
    setSaved(false);
    try {
      await api.saveSettings({
        manual_rules: manualRules,
        learned_profile: learnedProfile,
        repo_rules: repoRules,
        repo_learned_profile: repoLearnedProfile,
        ad_regex_blacklist: adRegex,
        min_inbox_score: minScore,
        min_repos_score: minReposScore,
        telegram_alert_score: alertScore,
      });
      setSaved(true);
      onReload();
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  const isRepos = kind === "repos";

  return (
    <div className="space-y-5">
      <div className="flex gap-1.5">
        <button
          type="button"
          onClick={() => setKind("news")}
          className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-[12.5px] transition ${
            !isRepos
              ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
              : "bg-ink-850 text-slate-400 hover:text-slate-200"
          }`}
        >
          <Flame className="h-3.5 w-3.5" />
          Правила: Новости
        </button>
        <button
          type="button"
          onClick={() => setKind("repos")}
          className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-[12.5px] transition ${
            isRepos
              ? "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500/30"
              : "bg-ink-850 text-slate-400 hover:text-slate-200"
          }`}
        >
          <Package className="h-3.5 w-3.5" />
          Правила: Репозитории
        </button>
      </div>

      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <label className="mb-2 block text-[13.5px] font-semibold text-white">
          {isRepos ? "Мои правила для репозиториев" : "Мои ручные правила (новости)"}
        </label>
        <p className="mb-2 text-[11.5px] leading-relaxed text-slate-500">
          {isRepos
            ? "Какие open-source инструменты пропускать, а какие считать мусором. Идёт в отдельный промпт для репозиториев."
            : "Что интересно и что нет. Идёт в системный промпт фильтра новостей."}
        </p>
        <textarea
          value={isRepos ? repoRules : manualRules}
          onChange={(e) => (isRepos ? setRepoRules(e.target.value) : setManualRules(e.target.value))}
          rows={7}
          className="w-full resize-y rounded-lg border border-ink-700 bg-ink-900 p-3 text-[12.5px] leading-relaxed text-slate-200 outline-none transition focus:border-emerald-500/50"
        />
      </section>

      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <div className="mb-2 flex items-center justify-between">
          <label className="text-[13.5px] font-semibold text-white">
            {isRepos ? "Профиль репозиториев, выученный ИИ" : "Профиль новостей, выученный ИИ"}
          </label>
          <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10.5px] text-violet-300">
            авто-обновляется
          </span>
        </div>
        <p className="mb-2 text-[11.5px] leading-relaxed text-slate-500">
          Обновляется по твоим 👍/👎 отдельно от второго потока. Можно править руками.
        </p>
        <textarea
          value={isRepos ? repoLearnedProfile : learnedProfile}
          onChange={(e) =>
            isRepos ? setRepoLearnedProfile(e.target.value) : setLearnedProfile(e.target.value)
          }
          rows={6}
          className="w-full resize-y rounded-lg border border-ink-700 bg-ink-900 p-3 text-[12.5px] leading-relaxed text-slate-200 outline-none transition focus:border-emerald-500/50"
        />
      </section>

      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <label className="mb-2 block text-[13.5px] font-semibold text-white">Regex-фильтр рекламы</label>
        <p className="mb-2 text-[11.5px] leading-relaxed text-slate-500">
          По одному паттерну на строку. Режет мусор до отправки в нейронку.
        </p>
        <textarea
          value={adRegex}
          onChange={(e) => setAdRegex(e.target.value)}
          rows={8}
          spellCheck={false}
          className="w-full resize-y rounded-lg border border-ink-700 bg-ink-900 p-3 font-mono text-[11.5px] leading-relaxed text-slate-300 outline-none transition focus:border-emerald-500/50"
        />
      </section>

      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <h3 className="mb-3 text-[13.5px] font-semibold text-white">Пороги</h3>

        <div className="space-y-4">
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-[12.5px] text-slate-400">Минимальный балл: Новости</label>
              <span className="rounded bg-ink-800 px-2 py-0.5 text-[12px] font-semibold tabular-nums text-emerald-300">
                {minScore}
              </span>
            </div>
            <input
              type="range"
              min="1"
              max="10"
              step="0.5"
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
              className="w-full accent-emerald-500"
            />
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-[12.5px] text-slate-400">Минимальный балл: Репозитории</label>
              <span className="rounded bg-ink-800 px-2 py-0.5 text-[12px] font-semibold tabular-nums text-cyan-300">
                {minReposScore}
              </span>
            </div>
            <input
              type="range"
              min="1"
              max="10"
              step="0.5"
              value={minReposScore}
              onChange={(e) => setMinReposScore(e.target.value)}
              className="w-full accent-cyan-500"
            />
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-[12.5px] text-slate-400">Порог Telegram-алерта</label>
              <span className="rounded bg-ink-800 px-2 py-0.5 text-[12px] font-semibold tabular-nums text-amber-300">
                {alertScore}
              </span>
            </div>
            <input
              type="range"
              min="5"
              max="10"
              step="0.1"
              value={alertScore}
              onChange={(e) => setAlertScore(e.target.value)}
              className="w-full accent-amber-500"
            />
          </div>
        </div>
      </section>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-emerald-500 px-4 py-2 text-[13px] font-semibold text-ink-950 transition hover:bg-emerald-400 disabled:opacity-40"
        >
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          Сохранить
        </button>
        {saved && <span className="text-[12.5px] text-emerald-400">Сохранено</span>}
      </div>
    </div>
  );
}
