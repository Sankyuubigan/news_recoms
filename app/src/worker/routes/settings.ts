import { Hono } from "hono";
import type { Env, PipelineRunRow } from "../types";

const settings = new Hono<{ Bindings: Env }>();

const EDITABLE_KEYS = new Set([
  "manual_rules",
  "learned_profile",
  "ad_regex_blacklist",
  "min_inbox_score",
  "telegram_alert_score",
  "learn_feedback_threshold",
  "youtube_deep_summary_enabled",
  "youtube_deep_summary_min_score",
]);

settings.get("/settings", async (c) => {
  const res = await c.env.DB.prepare(`SELECT key, value, updated_at FROM settings ORDER BY key`).all<{
    key: string;
    value: string;
    updated_at: string;
  }>();
  const map: Record<string, string> = {};
  for (const r of res.results ?? []) map[r.key] = r.value;
  return c.json({ settings: map });
});

settings.put("/settings", async (c) => {
  const body = (await c.req.json().catch(() => ({}))) as Record<string, unknown>;
  const entries = Object.entries(body).filter(([k]) => EDITABLE_KEYS.has(k));
  if (!entries.length) return c.json({ error: "nothing_to_update" }, 400);

  const stmt = c.env.DB.prepare(
    `INSERT INTO settings (key, value, updated_at)
     VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
     ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
  );
  await c.env.DB.batch(entries.map(([k, v]) => stmt.bind(k, String(v ?? ""))));

  return c.json({ ok: true, updated: entries.map(([k]) => k) });
});

settings.get("/runs", async (c) => {
  const raw = Number(c.req.query("limit"));
  const limit = Math.min(Math.max(Number.isFinite(raw) ? raw : 30, 1), 100);
  const res = await c.env.DB.prepare(`SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?`)
    .bind(limit)
    .all<PipelineRunRow>();

  const runs = (res.results ?? []).map((r) => {
    let providerStats: unknown = {};
    let errors: unknown = [];
    try {
      providerStats = JSON.parse(r.provider_stats_json ?? "{}");
    } catch {
      /* ignore */
    }
    try {
      errors = JSON.parse(r.errors_json ?? "[]");
    } catch {
      /* ignore */
    }
    return { ...r, provider_stats: providerStats, errors };
  });

  return c.json({ runs });
});

export default settings;
