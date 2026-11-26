import { useEffect, useState } from "react";
import { Check, CloudOff, Loader2, RefreshCw, Smartphone } from "lucide-react";
import type { SyncStatus } from "../api/types";

interface Props {
  status: SyncStatus;
  onConnect: () => void;
  onSyncNow: () => void;
}

const META: Record<SyncStatus["state"], { text: string; cls: string }> = {
  readonly: { text: "Режим чтения", cls: "bg-ink-850 text-slate-400" },
  idle: { text: "", cls: "" },
  pending: { text: "Синхронизация…", cls: "bg-ink-850 text-amber-300/90" },
  saving: { text: "Синхронизация…", cls: "bg-ink-850 text-amber-300/90" },
  saved: { text: "✓ Сохранено", cls: "bg-emerald-500/10 text-emerald-300" },
  error: { text: "Ошибка, повтор", cls: "bg-rose-500/10 text-rose-300" },
};

export default function SyncBanner({ status, onConnect, onSyncNow }: Props) {
  const [dismissed, setDismissed] = useState(false);
  const [online, setOnline] = useState(navigator.onLine);

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);

  // Режим чтения — ненавязчивая плашка с призывом подключить синхронизацию
  if (status.state === "readonly") {
    if (dismissed) return null;
    return (
      <div className="flex items-center gap-2 border-b border-ink-800 bg-ink-900/70 px-4 py-1.5 text-[11.5px] text-slate-400">
        <CloudOff className="h-3.5 w-3.5 shrink-0 text-slate-500" />
        <span className="min-w-0 flex-1 truncate">
          Режим чтения. Подключи синхронизацию (1 раз), чтобы сохранять лайки и «Просмотрено» между
          устройствами.
        </span>
        <button
          onClick={onConnect}
          className="shrink-0 rounded-md bg-emerald-500/15 px-2 py-1 font-medium text-emerald-300 transition hover:bg-emerald-500/25"
        >
          Подключить
        </button>
        <button
          onClick={() => setDismissed(true)}
          className="shrink-0 px-1 text-slate-600 transition hover:text-slate-400"
          title="Скрыть"
        >
          ✕
        </button>
      </div>
    );
  }

  const meta = META[status.state];
  if (!meta.text) return null;

  return (
    <div className={`flex items-center gap-2 border-b border-ink-800 px-4 py-1.5 text-[11.5px] ${meta.cls}`}>
      {status.state === "pending" || status.state === "saving" ? (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
      ) : status.state === "saved" ? (
        <Check className="h-3 w-3 shrink-0" />
      ) : (
        <CloudOff className="h-3 w-3 shrink-0" />
      )}
      <span className="min-w-0 flex-1 truncate">
        {meta.text}
        {status.message ? ` — ${status.message}` : ""}
        {!online ? " (нет сети)" : ""}
      </span>
      {status.state === "error" && (
        <button
          onClick={onSyncNow}
          className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 transition hover:bg-ink-800"
        >
          <RefreshCw className="h-3 w-3" />
          Повторить
        </button>
      )}
      <button
        onClick={onConnect}
        className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-slate-500 transition hover:bg-ink-800 hover:text-slate-300"
        title="Настройки синхронизации"
      >
        <Smartphone className="h-3 w-3" />
      </button>
    </div>
  );
}
