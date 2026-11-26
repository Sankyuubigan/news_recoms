import { useEffect, useState } from "react";
import { Loader2, Save } from "lucide-react";
import { api } from "../api/client";

interface Props {
  settings: Record<string, string>;
  onReload: () => void;
}

export default function RulesAndProfile({ settings, onReload }: Props) {
  const [manualRules, setManualRules] = useState("");
  const [learnedProfile, setLearnedProfile] = useState("");
  const [adRegex, setAdRegex] = useState("");
  const [minScore, setMinScore] = useState("6.5");
  const [alertScore, setAlertScore] = useState("9.0");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setManualRules(settings.manual_rules ?? "");
    setLearnedProfile(settings.learned_profile ?? "");
    setAdRegex(settings.ad_regex_blacklist ?? "");
    setMinScore(settings.min_inbox_score ?? "6.5");
    setAlertScore(settings.telegram_alert_score ?? "9.0");
  }, [settings]);

  const save = async () => {
    setSaving(true);
    setSaved(false);
    try {
      await api.saveSettings({
        manual_rules: manualRules,
        learned_profile: learnedProfile,
        ad_regex_blacklist: adRegex,
        min_inbox_score: minScore,
        telegram_alert_score: alertScore,
      });
      setSaved(true);
      onReload();
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <label className="mb-2 block text-[13.5px] font-semibold text-white">Мои ручные правила</label>
        <p className="mb-2 text-[11.5px] leading-relaxed text-slate-500">
          Что интересно и что нет. Идёт в системный промпт фильтра.
        </p>
        <textarea
          value={manualRules}
          onChange={(e) => setManualRules(e.target.value)}
          rows={7}
          className="w-full resize-y rounded-lg border border-ink-700 bg-ink-900 p-3 text-[12.5px] leading-relaxed text-slate-200 outline-none transition focus:border-emerald-500/50"
        />
      </section>

      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <div className="mb-2 flex items-center justify-between">
          <label className="text-[13.5px] font-semibold text-white">Профиль, выученный ИИ</label>
          <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10.5px] text-violet-300">
            авто-обновляется
          </span>
        </div>
        <p className="mb-2 text-[11.5px] leading-relaxed text-slate-500">
          Обновляется по твоим 👍/👎. Можно править руками — правки учитываются.
        </p>
        <textarea
          value={learnedProfile}
          onChange={(e) => setLearnedProfile(e.target.value)}
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
              <label className="text-[12.5px] text-slate-400">Минимальный балл для ленты</label>
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
