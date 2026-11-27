import { useEffect, useState } from "react";
import { Check, ExternalLink, KeyRound, Loader2, Play, RefreshCw, Smartphone, Trash2 } from "lucide-react";
import {
  actionsUrl,
  detectAppRepo,
  getDataRepoOverride,
  getRepo,
  getRepoInfo,
  getToken,
  repoSlug,
  setDataRepoOverride,
} from "../api/github";
import { api, store } from "../api/client";

interface Props {
  onOpenModal: () => void;
  onRefresh: () => Promise<void>;
  onNotify: (message: string) => void;
}

export default function SyncSettings({ onOpenModal, onRefresh, onNotify }: Props) {
  const [repoInfo, setRepoInfo] = useState<string | null>(null);
  const [canActions, setCanActions] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const token = getToken();
  const repo = getRepo();
  const override = getDataRepoOverride();
  const appRepo = detectAppRepo();

  useEffect(() => {
    if (!token || !repo) return;
    setChecking(true);
    getRepoInfo(repo, token)
      .then((info) => {
        setRepoInfo(`${info.fullName}${info.isPrivate ? " (приватный)" : ""}`);
        setCanActions(info.canPush);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setChecking(false));
  }, [token, repo?.owner, repo?.repo]);

  const runIngest = async () => {
    setRunning(true);
    setError(null);
    try {
      await api.runIngest();
      onNotify("Сбор новостей запущен — результаты появятся через пару минут");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    try {
      await onRefresh();
      onNotify("Лента обновлена");
    } finally {
      setRefreshing(false);
    }
  };

  const resetRepo = () => {
    setDataRepoOverride(null);
    void api.refresh();
    onNotify("Репозиторий данных сброшен на текущий");
  };

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <div className="mb-3 flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-emerald-400" />
          <h3 className="text-[13.5px] font-semibold text-white">Подключение записи</h3>
          {checking && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" />}
        </div>

        {token ? (
          <div className="space-y-2.5">
            <div className="flex items-center gap-2 rounded-lg bg-emerald-500/10 px-3 py-2 text-[12.5px] text-emerald-300">
              <Check className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">
                {repoInfo ? `Подключено к ${repoInfo}` : "Токен сохранён"}
              </span>
            </div>
            <p className="text-[11.5px] leading-relaxed text-slate-500">
              Ветка данных: <code className="text-slate-400">{repo ? repoSlug(repo) : "—"} → data</code>
              {override ? " (указан вручную)" : " (текущий репозиторий)"}
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={onOpenModal}
                className="flex items-center gap-1.5 rounded-lg bg-ink-800 px-3 py-2 text-[12.5px] text-slate-300 transition hover:bg-ink-700"
              >
                <Smartphone className="h-3.5 w-3.5" />
                QR для телефона
              </button>
              {override && (
                <button
                  onClick={resetRepo}
                  className="flex items-center gap-1.5 rounded-lg bg-ink-800 px-3 py-2 text-[12.5px] text-slate-400 transition hover:bg-ink-700 hover:text-rose-300"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Сбросить репозиторий
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-2.5">
            <p className="text-[12.5px] leading-relaxed text-slate-400">
              Сейчас режим чтения: новости видны, но лайки и «Просмотрено» не сохраняются между
              устройствами. Подключи токен один раз — и всё будет синхронизироваться через ветку{" "}
              <code className="text-slate-400">data</code>.
            </p>
            <button
              onClick={onOpenModal}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3.5 py-2 text-[13px] font-semibold text-ink-950 transition hover:bg-emerald-400"
            >
              <KeyRound className="h-3.5 w-3.5" />
              Подключить синхронизацию
            </button>
          </div>
        )}

        {error && <p className="mt-2 text-[12px] leading-relaxed text-rose-400">{error}</p>}
      </section>

      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <h3 className="mb-3 text-[13.5px] font-semibold text-white">Действия</h3>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={runIngest}
            disabled={running || !token}
            className="flex items-center gap-1.5 rounded-lg bg-emerald-500/15 px-3 py-2 text-[12.5px] font-medium text-emerald-300 transition hover:bg-emerald-500/25 disabled:opacity-40"
            title={token ? "Запустить workflow Ingest" : "Нужна синхронизация с правом Actions: write"}
          >
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            Запустить сбор новостей
          </button>
          <button
            onClick={refresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-lg bg-ink-800 px-3 py-2 text-[12.5px] text-slate-300 transition hover:bg-ink-700 disabled:opacity-40"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            Обновить ленту
          </button>
          {appRepo && (
            <a
              href={actionsUrl(appRepo)}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-lg bg-ink-800 px-3 py-2 text-[12.5px] text-slate-400 transition hover:bg-ink-700 hover:text-slate-200"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Actions
            </a>
          )}
        </div>
        {!canActions && token && !error && (
          <p className="mt-2 text-[11.5px] leading-relaxed text-slate-600">
            Если кнопка сбора не сработает — у токена нет права <b>Actions: Read and write</b>. Можно
            запустить вручную на вкладке Actions.
          </p>
        )}
      </section>

      <section className="rounded-xl border border-ink-800 bg-ink-850 p-4">
        <h3 className="mb-2 text-[13.5px] font-semibold text-white">Где лежат данные</h3>
        <p className="text-[11.5px] leading-relaxed text-slate-500">
          Приложение:{" "}
          <code className="text-slate-400">
            {appRepo ? repoSlug(appRepo) : "—"} → main (GitHub Pages)
          </code>
          <br />
          Данные: <code className="text-slate-400">{repo ? repoSlug(repo) : "—"} → data</code>
          <br />
          Файлы: <code className="text-slate-400">feed.json</code>,{" "}
          <code className="text-slate-400">user_state.json</code>,{" "}
          <code className="text-slate-400">config.json</code>,{" "}
          <code className="text-slate-400">runs.json</code>
        </p>
      </section>
    </div>
  );
}
