import { Hono } from "hono";
import type { Env } from "../types";
import {
  createSessionToken,
  safeEqual,
  sessionCookie,
  clearSessionCookie,
  sessionCookieName,
  verifySessionToken,
} from "../auth";

const auth = new Hono<{ Bindings: Env }>();

auth.post("/login", async (c) => {
  const body = (await c.req.json().catch(() => ({}))) as { password?: string };
  const password = typeof body.password === "string" ? body.password : "";
  if (!c.env.APP_PASSWORD || !safeEqual(password, c.env.APP_PASSWORD)) {
    return c.json({ error: "invalid_password" }, 401);
  }
  const token = await createSessionToken(c.env.SESSION_SECRET);
  c.header("Set-Cookie", sessionCookie(token));
  return c.json({ ok: true });
});

auth.post("/logout", (c) => {
  c.header("Set-Cookie", clearSessionCookie());
  return c.json({ ok: true });
});

auth.get("/me", async (c) => {
  const header = c.req.header("Cookie") ?? "";
  let token: string | undefined;
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === sessionCookieName) token = rest.join("=");
  }
  const ok = await verifySessionToken(c.env.SESSION_SECRET, token);
  if (!ok) return c.json({ authenticated: false }, 401);
  return c.json({ authenticated: true });
});

export default auth;
