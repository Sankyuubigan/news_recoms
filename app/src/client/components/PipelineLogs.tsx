import { AlertCircle, CheckCircle2, Clock, XCircle } from "lucide-react";
import type { PipelineRun } from "../api/client";
import { relativeTime } from "../utils";

interface Props {
  runs: PipelineRun[];
}

const STATUS_META = {
  success: { icon: CheckCircle2, cls: "text-emerald-400", label: "успех" },
  partial: { icon: AlertCircle, cls: "text-amber-400", label: "частично" },
  failed: { icon: XCircle, cls: "text-rose-400", label: "провал" },
  running: { icon: Clock, cls: "text-blue-400", label: "идёт" },
} as const;

export default function PipelineLogs({ runs }: Props) {
  if (runs.length === 0) {
    return (
      <p className="py-12 text-center text-[13px] text-slate-500">
        Запусков пока не было. Пайплайн пишет сюда статистику каждого прогона.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {runs.map((r) => {
        const meta = STATUS_META[r.status] ?? STATUS_META.running;
        const Icon = meta.icon;
        const providers = Object.entries(r.provider_stats ?? {});

        return (
          <div key={r.id} className="rounded-xl border border-ink-800 bg-ink-850 p-3.5">
            <div className="flex items-center gap-2">
              <Icon className={`h-4 w-4 ${meta.cls}`} />
              <span className="text-[13px] font-medium text-slate-200">{meta.label}</span>
              <span className="text-[11.5px] text-slate-500">{relativeTime(r.started_at)}</span>
              <span className="ml-auto text-[11px] tabular-nums text-slate-600">#{r.id}</span>
            </div>

            <div className="mt-2.5 grid grid-cols-3 gap-2 sm:grid-cols-6">
              {[
                { label: "собрано", value: r.fetched_count },
                { label: "реклама", value: r.ad_blocked_count },
                { label: "дубли", value: r.dedup_skipped_count },
                { label: "в ИИ", value: r.ai_processed_count },
                { label: "в ленту", value: r.inbox_added_count },
                { label: "отсеяно", value: r.rejected_count },
              ].map((s) => (
                <div key={s.label} className="rounded-lg bg-ink-900 px-2 py-1.5">
                  <div className="text-[15px] font-semibold tabular-nums text-slate-200">{s.value}</div>
                  <div className="text-[10.5px] text-slate-500">{s.label}</div>
                </div>
              ))}
            </div>

            {providers.length > 0 && (
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {providers.map(([name, st]) => (
                  <span
                    key={name}
                    className="rounded bg-ink-900 px-1.5 py-0.5 text-[10.5px] tabular-nums text-slate-400"
                  >
                    {name}: <span className="text-emerald-400">{st.ok ?? 0}</span>/
                    <span className="text-rose-400">{st.err ?? 0}</span>
                  </span>
                ))}
              </div>
            )}

            {r.errors.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer text-[11.5px] text-amber-400/80">
                  Ошибки ({r.errors.length})
                </summary>
                <pre className="mt-1.5 max-h-40 overflow-auto rounded-lg bg-ink-950 p-2 text-[10.5px] leading-relaxed text-slate-400">
                  {JSON.stringify(r.errors, null, 2)}
                </pre>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}
