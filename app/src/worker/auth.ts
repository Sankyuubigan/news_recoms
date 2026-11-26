import type { Context, MiddlewareHandler } from "hono";
import type { Env } from "./types";

const COOKIE_NAME = "pf_session";
const SESSION_TTL_SEC = 7776000; // 90 дней

const enc = new TextEncoder();

function b64urlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(str: string): Uint8Array {
  const pad = str.length % 4 === 0 ? "" : "=".repeat(4 - (str.length % 4));
  const bin = atob(str.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
    "verify",
  ]);
}

/** Constant-time string comparison (UTF-8 aware, length leak acceptable for a single-user app). */
export function safeEqual(a: string, b: string): boolean {
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  let diff = ab.length ^ bb.length;
  const len = Math.max(ab.length, bb.length);
  for (let i = 0; i < len; i++) {
    diff |= (ab[i] ?? 0) ^ (bb[i] ?? 0);
  }
  return diff === 0;
}

export async function createSessionToken(secret: string): Promise<string> {
  const payload: string = b64urlEncode(
    enc.encode(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + SESSION_TTL_SEC })),
  );
  const key = await hmacKey(secret);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(payload)));
  return `${payload}.${b64urlEncode(sig)}`;
}

export async function verifySessionToken(secret: string, token: string | undefined): Promise<boolean> {
  if (!token) return false;
  const idx = token.lastIndexOf(".");
  if (idx <= 0) return false;
  const payload = token.slice(0, idx);
  const sigPart = token.slice(idx + 1);
  let sig: Uint8Array;
  try {
    sig = b64urlDecode(sigPart);
  } catch {
    return false;
  }
  const key = await hmacKey(secret);
  const ok = await crypto.subtle.verify("HMAC", key, sig, enc.encode(payload));
  if (!ok) return false;
  try {
    const data = JSON.parse(new TextDecoder().decode(b64urlDecode(payload))) as { exp?: number };
    return typeof data.exp === "number" && data.exp > Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}

function readCookie(c: Context, name: string): string | undefined {
  const header = c.req.header("Cookie") ?? "";
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return rest.join("=");
  }
  return undefined;
}

export const sessionCookieName = COOKIE_NAME;

export function sessionCookie(token: string): string {
  return `${COOKIE_NAME}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${SESSION_TTL_SEC}`;
}

export function clearSessionCookie(): string {
  return `${COOKIE_NAME}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`;
}

/** Middleware: require a valid session cookie on /api/v1/* */
export const requireSession: MiddlewareHandler<{ Bindings: Env }> = async (c, next) => {
  const token = readCookie(c, COOKIE_NAME);
  const ok = await verifySessionToken(c.env.SESSION_SECRET, token);
  if (!ok) return c.json({ error: "unauthorized" }, 401);
  await next();
};

/** Middleware: require Bearer INGEST_SECRET on /api/internal/* */
export const requireIngest: MiddlewareHandler<{ Bindings: Env }> = async (c, next) => {
  const header = c.req.header("Authorization") ?? "";
  const m = /^Bearer\s+(.+)$/i.exec(header.trim());
  if (!m || !safeEqual(m[1], c.env.INGEST_SECRET)) {
    return c.json({ error: "forbidden" }, 403);
  }
  await next();
};
