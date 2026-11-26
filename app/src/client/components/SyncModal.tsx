import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { Check, Copy, ExternalLink, KeyRound, Loader2, Smartphone, X } from "lucide-react";
import {
  detectAppRepo,
  getDataRepoOverride,
  getRepo,
  getRepoInfo,
  getToken,
  parseRepoSlug,
  repoSlug,
  setDataRepoOverride,
  setToken,
  tokenCreateUrl,
} from "../api/github";
import { api, store } from "../api/client";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function SyncModal({ open, onClose }: Props) {
  const [token, setTokenInput] = useState("");
  const [dataRepo, setDataRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const appRepo = detectAppRepo();
  const currentRepo = getRepo();

  useEffect(() => {
    if (!open) return;
    setTokenInput(getToken() ?? "");
    setDataRepo(getDataRepoOverride() ? repoSlug(getDataRepoOverride()!) : "");
    setError(null);
    setConnected(store.hasToken() ? store.getRepoLabel() : null);
    setQr(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const saveRepoOverride = () => {
    const trimmed = dataRepo.trim();
    if (!trimmed) {
      setDataRepoOverride(null);
      return true;
    }
    if (!parseRepoSlug(trimmed)) {
      setError("Формат: owner/repo");
      return false;
    }
    setDataRepoOverride(trimmed);
    return true;
  };

  const connect = async () => {
    const t = token.trim();
    if (!t) return;
    if (!saveRepoOverride()) return;

    setBusy(true);
    setError(null);
    try {
      setToken(t);
      const repo = getRepo();
      if (!repo) throw new Error("Не удалось определить репозиторий");
      const info = await getRepoInfo(repo, t);
      if (!info.canPush) {
        throw new Error(
          "У токена нет права Contents: Read and write на этот репозиторий. Проверь настройки токена.",
        );
      }
      await api.connect(t);
      setConnected(`${info.fullName}${info.isPrivate ? " (приватный)" : ""}`);
    } catch (e) {
      setToken(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setToken(null);
    await api.disconnect();
    setConnected(null);
    setTokenInput("");
    setQr(null);
  };

  const makeQr = async () => {
    const t = getToken();
    const repo = getRepo();
    if (!t || !repo) return;
    const base = `${window.location.origin}${window.location.pathname}`.replace(/\/index\.html$/, "/");
    const link = `${base}#sync_token=${encodeURIComponent(t)}&repo=${encodeURIComponent(repoSlug(repo))}`;
    try {
      const dataUrl = await QRCode.toDataURL(link, {
        errorCorrectionLevel: "M",
        margin: 1,
        width: 512,
        color: { dark: "#08090c", light: "#ffffff" },
      });
      setQr(dataUrl);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось построить QR-код");
    }
  };

  const copyLink = async () => {
    const t = getToken();
    const repo = getRepo();
    if (!t || !repo) return;
    const base = `${window.location.origin}${window.location.pathname}`.replace(/\/index\.html$/, "/");
    const link = `${base}#sync_token=${encodeURIComponent(t)}&repo=${encodeURIComponent(repoSlug(repo))}`;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Браузер не дал доступ к буферу обмена");
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="pf-slide-up max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-t-2xl border border-ink-700 bg-ink-900 p-5 sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start gap-2">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15">
            <KeyRound className="h-4.5 w-4.5 text-emerald-300" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-semibold text-white">Синхронизация</h2>
            <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
              Лайки, «Просмотрено» и настройки сохраняются в ветку <code className="text-slate-400">data</code>{" "}
              твоего репозитория.
            </p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-slate-500 hover:bg-ink-800 hover:text-slate-300">
            <X className="h-4 w-4" />
          </button>
        </div>

        {connected ? (
          <div className="mb-4 flex items-center gap-2 rounded-lg bg-emerald-500/10 px-3 py-2.5 text-[12.5px] text-emerald-300">
            <Check className="h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1 truncate">Подключено к {connected}</span>
            <button
              onClick={disconnect}
              className="shrink-0 rounded px-1.5 py-0.5 text-[11.5px] text-slate-400 transition hover:bg-ink-800 hover:text-rose-300"
            >
              Отключить
            </button>
          </div>
        ) : null}

        <div className="space-y-3">
          <div>
            <label className="mb-1.5 block text-[12.5px] font-medium text-slate-300">
              GitHub Fine-Grained Token
            </label>
            <input
              type="password"
              value={token}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="github_pat_…"
              autoComplete="off"
              spellCheck={false}
              className="w-full rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-[12px] text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-emerald-500/50"
            />
            <ol className="mt-2 space-y-1 text-[11.5px] leading-relaxed text-slate-500">
              <li>
                1.{" "}
                <a
                  href={currentRepo ? tokenCreateUrl(currentRepo) : "https://github.com/settings/personal-access-tokens/new"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 text-emerald-400 underline decoration-emerald-500/30 underline-offset-2 hover:text-emerald-300"
                >
                  Создай токен
                  <ExternalLink className="h-2.5 w-2.5" />
                </a>{" "}
                — выбери <b className="text-slate-400">только этот репозиторий</b>
              </li>
              <li>
                2. Permissions → <b className="text-slate-400">Contents: Read and write</b>
              </li>
              <li>
                3. Для кнопки «Запустить сбор» ещё <b className="text-slate-400">Actions: Read and write</b>
              </li>
            </ol>
          </div>

          <div>
            <label className="mb-1.5 block text-[12.5px] font-medium text-slate-300">
              Репозиторий для данных <span className="text-slate-600">(необязательно)</span>
            </label>
            <input
              value={dataRepo}
              onChange={(e) => setDataRepo(e.target.value)}
              placeholder={appRepo ? `${appRepo.owner}/${appRepo.repo}` : "owner/repo"}
              spellCheck={false}
              className="w-full rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-[12px] text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-emerald-500/50"
            />
            <p className="mt-1 text-[11px] text-slate-600">
              Оставь пустым, чтобы держать ветку <code>data</code> в этом же репозитории.
            </p>
          </div>

          {error && <p className="text-[12px] leading-relaxed text-rose-400">{error}</p>}

          <button
            onClick={connect}
            disabled={busy || !token.trim()}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-500 py-2.5 text-[13px] font-semibold text-ink-950 transition hover:bg-emerald-400 disabled:opacity-40"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {connected ? "Проверить и обновить" : "Подключить"}
          </button>

          {connected && (
            <div className="rounded-lg border border-ink-800 bg-ink-850 p-3">
              <div className="mb-2 flex items-center gap-2">
                <Smartphone className="h-4 w-4 text-slate-400" />
                <span className="text-[12.5px] font-medium text-slate-300">Подключить телефон</span>
              </div>
              {qr ? (
                <div className="flex flex-col items-center gap-2">
                  <img src={qr} alt="QR-код для подключения телефона" className="w-52 rounded-lg bg-white p-2" />
                  <p className="text-center text-[11px] leading-relaxed text-slate-500">
                    Открой камеру на телефоне и наведи на код. Токен сохранится на телефоне, из адресной
                    строки он сразу исчезнет.
                  </p>
                  <button
                    onClick={copyLink}
                    className="flex items-center gap-1.5 rounded-md bg-ink-800 px-2.5 py-1.5 text-[11.5px] text-slate-300 transition hover:bg-ink-700"
                  >
                    {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
                    {copied ? "Ссылка скопирована" : "Скопировать ссылку"}
                  </button>
                </div>
              ) : (
                <button
                  onClick={makeQr}
                  className="w-full rounded-md bg-ink-800 py-2 text-[12px] text-slate-300 transition hover:bg-ink-700"
                >
                  Сгенерировать QR-код
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
