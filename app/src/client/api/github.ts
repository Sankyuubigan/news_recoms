/**
 * Низкоуровневый клиент GitHub REST API.
 * Никаких сторонних серверов: читаем и пишем файлы ветки `data` напрямую.
 */

const API = "https://api.github.com";
export const DATA_BRANCH = "data";

const LS_TOKEN = "nr.token";
const LS_DATA_REPO = "nr.dataRepo";

export interface RepoRef {
  owner: string;
  repo: string;
}

export function repoSlug(r: RepoRef): string {
  return `${r.owner}/${r.repo}`;
}

export function parseRepoSlug(value: string): RepoRef | null {
  const m = /^\s*([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\s*$/.exec(value);
  return m ? { owner: m[1], repo: m[2] } : null;
}

export class GithubError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "GithubError";
    this.status = status;
  }
}

/** Конфликт sha при записи (кто-то успел обновить файл). */
export class ConflictError extends GithubError {
  constructor(message = "Файл изменился на сервере") {
    super(409, message);
    this.name = "ConflictError";
  }
}

// ------------------------------------------------------------------ storage

export function getToken(): string | null {
  try {
    return localStorage.getItem(LS_TOKEN);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(LS_TOKEN, token);
    else localStorage.removeItem(LS_TOKEN);
  } catch {
    /* приватный режим */
  }
}

export function getDataRepoOverride(): RepoRef | null {
  try {
    const raw = localStorage.getItem(LS_DATA_REPO);
    return raw ? parseRepoSlug(raw) : null;
  } catch {
    return null;
  }
}

export function setDataRepoOverride(value: string | null): void {
  try {
    if (value && parseRepoSlug(value)) localStorage.setItem(LS_DATA_REPO, value.trim());
    else localStorage.removeItem(LS_DATA_REPO);
  } catch {
    /* ignore */
  }
}

/**
 * Определяет репозиторий приложения из URL вида https://<owner>.github.io/<repo>/
 * с фоллбеком на переменные сборки (локальная разработка).
 */
export function detectAppRepo(): RepoRef | null {
  const host = window.location.hostname;
  if (host.endsWith(".github.io")) {
    const owner = host.slice(0, -".github.io".length);
    const first = window.location.pathname.split("/").filter(Boolean)[0];
    const repo = first && !first.endsWith(".html") ? first : `${owner}.github.io`;
    return { owner, repo };
  }
  const owner = import.meta.env.VITE_GITHUB_OWNER as string | undefined;
  const repo = import.meta.env.VITE_GITHUB_REPO as string | undefined;
  if (owner && repo) return { owner, repo };
  return null;
}

/**
 * Репозиторий данных по умолчанию: `VITE_DATA_REPO` из сборки или сам репозиторий
 * приложения. Владелец задаёт приватный репо через переменную репозитория
 * (Settings → Variables → Actions), которая в форки не наследуется — поэтому
 * форкер по умолчанию пишет в свой репозиторий.
 */
export function getDataRepoDefault(): RepoRef | null {
  const raw = import.meta.env.VITE_DATA_REPO as string | undefined;
  const parsed = raw ? parseRepoSlug(raw) : null;
  return parsed ?? detectAppRepo();
}

/** Репозиторий с данными: пользовательский override или репозиторий по умолчанию. */
export function getRepo(): RepoRef | null {
  return getDataRepoOverride() ?? getDataRepoDefault();
}

// ------------------------------------------------------------------- base64

export function b64ToUtf8(b64: string): string {
  const clean = b64.replace(/\s/g, "");
  const bin = atob(clean);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder("utf-8").decode(bytes);
}

export function utf8ToB64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

// --------------------------------------------------------------------- http

async function gh(path: string, token: string | null, init: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    ...((init.headers as Record<string, string> | undefined) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${API}${path}`, { ...init, headers, cache: "no-store" });
}

async function errorText(res: Response): Promise<string> {
  try {
    const j = (await res.json()) as { message?: string };
    return j.message ?? res.statusText;
  } catch {
    return res.statusText;
  }
}

async function readRaw<T>(repo: RepoRef, path: string): Promise<RemoteJson<T> | null> {
  const url = `https://raw.githubusercontent.com/${repoSlug(repo)}/${DATA_BRANCH}/${path}?t=${Date.now()}`;
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new GithubError(res.status, `Не удалось прочитать ${path} (${res.status})`);
  return { data: (await res.json()) as T, sha: "" };
}

export interface RemoteJson<T> {
  data: T;
  sha: string;
}

const RATE_LIMIT_MESSAGE =
  "Лимит GitHub API исчерпан — подожди пару минут и обнови страницу";

/**
 * Читает файл через Contents API с media-type `raw+json`.
 *
 * Зачем отдельный путь: обычный JSON-ответ Contents API отдаёт содержимое в base64
 * и работает только до 1 МБ. `feed.json` уже перевалил за 760 КБ, а base64
 * добавляет ещё ~33% → лимит был бы превышен, и лента навсегда сломалась бы.
 * `raw+json` официально обслуживает файлы до 100 МБ и отдаёт их как есть.
 *
 * `sha` здесь не возвращается — этот путь только для read-only файлов
 * (`feed.json`, `runs.json`), которые фронтенд никогда не пишет.
 */
async function readViaApiRaw<T>(
  repo: RepoRef,
  path: string,
  token: string,
): Promise<RemoteJson<T> | null> {
  const res = await gh(`/repos/${repoSlug(repo)}/contents/${path}?ref=${DATA_BRANCH}`, token, {
    headers: { Accept: "application/vnd.github.raw+json" },
  });

  if (res.status === 404) return null;
  if (res.status === 401) throw new GithubError(401, "Токен недействителен или истёк");
  if (res.status === 403 || res.status === 429) throw new GithubError(res.status, RATE_LIMIT_MESSAGE);
  if (!res.ok) {
    throw new GithubError(res.status, `Не удалось прочитать ${path}: ${await errorText(res)}`);
  }
  return { data: (await res.json()) as T, sha: "" };
}

/**
 * Читает JSON-файл из ветки `data`.
 *
 * - `needSha: true` (по умолчанию) — через Contents API в JSON: нужен `sha` для записи.
 *   Работает только с файлами < 1 МБ, поэтому для `user_state.json` / `config.json`.
 * - `needSha: false` — через `raw+json`: файлы до 100 МБ, но без `sha`.
 *   Для read-only `feed.json` / `runs.json`.
 *
 * Без токена доступен только публичный `raw.githubusercontent.com`. Для приватного
 * репозитория он отдаёт 404, поэтому fallback на raw при наличии токена отключён:
 * иначе лимит API молча превращался бы в «пустую ленту».
 */
export async function readJson<T>(
  repo: RepoRef,
  path: string,
  token: string | null,
  needSha = true,
): Promise<RemoteJson<T> | null> {
  if (!token) return readRaw<T>(repo, path);
  if (!needSha) return readViaApiRaw<T>(repo, path, token);

  const res = await gh(`/repos/${repoSlug(repo)}/contents/${path}?ref=${DATA_BRANCH}`, token);

  if (res.status === 404) return null;
  if (res.status === 401) throw new GithubError(401, "Токен недействителен или истёк");
  if (res.status === 403 || res.status === 429) throw new GithubError(res.status, RATE_LIMIT_MESSAGE);
  if (!res.ok) throw new GithubError(res.status, `Не удалось прочитать ${path}: ${await errorText(res)}`);

  const j = (await res.json()) as { content?: string; sha?: string; encoding?: string };
  if (!j.content) {
    // Файл больше 1 МБ: Contents API отдаёт encoding "none" и пустой content.
    // Такие файлы обязан читать путь с `needSha: false`.
    if (j.encoding === "none") {
      throw new GithubError(
        413,
        `${path} больше 1 МБ — читай его через readViaApiRaw (needSha: false)`,
      );
    }
    return null;
  }
  return { data: JSON.parse(b64ToUtf8(j.content)) as T, sha: j.sha ?? "" };
}

/** Записывает JSON-файл в ветку `data`. Возвращает новый sha. */
export async function writeJson<T>(
  repo: RepoRef,
  path: string,
  data: T,
  token: string,
  sha: string,
  message: string,
): Promise<string> {
  const body: Record<string, unknown> = {
    message,
    content: utf8ToB64(JSON.stringify(data, null, 2) + "\n"),
    branch: DATA_BRANCH,
  };
  if (sha) body.sha = sha;

  const res = await gh(`/repos/${repoSlug(repo)}/contents/${path}`, token, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (res.status === 409) throw new ConflictError();

  if (!res.ok) {
    const text = await errorText(res);
    if (res.status === 422 && /sha/i.test(text)) throw new ConflictError(text);
    if (res.status === 401) throw new GithubError(401, "Токен недействителен или истёк");
    if (res.status === 404) {
      throw new GithubError(
        404,
        "Ветка data не найдена. Запусти workflow Ingest хотя бы один раз — он создаст её.",
      );
    }
    throw new GithubError(res.status, `Не удалось сохранить ${path}: ${text}`);
  }

  const j = (await res.json()) as { content?: { sha?: string } };
  return j.content?.sha ?? "";
}

// ---------------------------------------------------------------- meta calls

export interface RepoInfo {
  fullName: string;
  defaultBranch: string;
  canPush: boolean;
  isPrivate: boolean;
}

export async function getRepoInfo(repo: RepoRef, token: string): Promise<RepoInfo> {
  const res = await gh(`/repos/${repoSlug(repo)}`, token);
  if (res.status === 404) {
    throw new GithubError(404, "Репозиторий не найден или у токена нет доступа к нему");
  }
  if (res.status === 401) throw new GithubError(401, "Токен недействителен или истёк");
  if (!res.ok) throw new GithubError(res.status, await errorText(res));

  const j = (await res.json()) as {
    full_name?: string;
    default_branch?: string;
    private?: boolean;
    permissions?: { push?: boolean };
  };
  return {
    fullName: j.full_name ?? repoSlug(repo),
    defaultBranch: j.default_branch ?? "main",
    canPush: Boolean(j.permissions?.push),
    isPrivate: Boolean(j.private),
  };
}

/** Запускает workflow Ingest через workflow_dispatch. Нужно право Actions: write. */
export async function dispatchIngest(repo: RepoRef, token: string): Promise<void> {
  const info = await getRepoInfo(repo, token);
  const res = await gh(`/repos/${repoSlug(repo)}/actions/workflows/ingest.yml/dispatches`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: info.defaultBranch }),
  });

  if (res.status === 204) return;
  if (res.status === 403 || res.status === 404) {
    throw new GithubError(
      res.status,
      "Токену нужно право Actions: Read and write (или запусти сбор вручную на вкладке Actions)",
    );
  }
  throw new GithubError(res.status, `Не удалось запустить сбор: ${await errorText(res)}`);
}

export function actionsUrl(repo: RepoRef): string {
  return `https://github.com/${repoSlug(repo)}/actions/workflows/ingest.yml`;
}

export function tokenCreateUrl(repo: RepoRef): string {
  const params = new URLSearchParams({
    name: "NewsRecoms sync",
    description: "Синхронизация ленты: ветка data",
    "scopes": "",
  });
  return `https://github.com/settings/personal-access-tokens/new?${params.toString()}`;
}
