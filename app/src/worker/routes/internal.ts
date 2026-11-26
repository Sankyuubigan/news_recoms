import { Hono } from "hono";
import type { Env, IngestItem, ItemRow, SourceRow } from "../types";

const internal = new Hono<{ Bindings: Env }>();

function parseJsonArray<T>(raw: string | null | undefined, fallback: T[]): T[] {
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : fallback;
  } catch {
    return fallback;
  }
}

/** Всё, что нужно пайплайну за один запрос. */
internal.get("/bootstrap", async (c) => {
  const db = c.env.DB;

  const [sourcesRes, settingsRes, urlsRes, hashesRes, clustersRes, pendingRes] = await Promise.all([
    db.prepare(`SELECT * FROM sources WHERE enabled = 1 ORDER BY type, name`).all<SourceRow>(),
    db.prepare(`SELECT key, value FROM settings`).all<{ key: string; value: string }>(),
    db
      .prepare(
        `SELECT url FROM items WHERE fetched_at >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-14 days')`,
      )
      .all<{ url: string }>(),
    db
      .prepare(
        `SELECT content_hash FROM items WHERE fetched_at >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-14 days')`,
      )
      .all<{ content_hash: string }>(),
    db
      .prepare(
        `SELECT DISTINCT cluster_slug, title_ru FROM items
          WHERE cluster_slug IS NOT NULL
            AND status IN ('inbox','saved','archived')
            AND published_at >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-48 hours')
          LIMIT 200`,
      )
      .all<{ cluster_slug: string; title_ru: string | null }>(),
    db
      .prepare(
        `SELECT i.*, s.identifier AS source_identifier, s.name AS source_name
           FROM items i JOIN sources s ON s.id = i.source_id
          WHERE i.status = 'pending_ai' ORDER BY i.published_at ASC LIMIT 50`,
      )
      .all<ItemRow & { source_identifier: string; source_name: string }>(),
  ]);

  const settings: Record<string, string> = {};
  for (const r of settingsRes.results ?? []) settings[r.key] = r.value;

  return c.json({
    sources: sourcesRes.results ?? [],
    settings,
    recent_urls: (urlsRes.results ?? []).map((r) => r.url),
    recent_hashes: (hashesRes.results ?? []).map((r) => r.content_hash),
    recent_clusters: clustersRes.results ?? [],
    pending_ai_items: (pendingRes.results ?? []).map((r) => ({
      ...r,
      key_points: parseJsonArray<string>(r.key_points_json, []),
      tags: parseJsonArray<string>(r.tags_json, []),
    })),
  });
});

/** Батчевый upsert обработанных элементов + схлопывание кластеров. */
internal.post("/ingest", async (c) => {
  const db = c.env.DB;
  const body = (await c.req.json().catch(() => ({}))) as { items?: IngestItem[] };
  const items = Array.isArray(body.items) ? body.items : [];
  if (!items.length) return c.json({ ok: true, inserted: 0, merged: 0, alerts: [] });

  // source map
  const srcRes = await db.prepare(`SELECT id, identifier, name FROM sources`).all<{
    id: number;
    identifier: string;
    name: string;
  }>();
  const sourceByKey = new Map<string, { id: number; name: string }>();
  for (const s of srcRes.results ?? []) sourceByKey.set(s.identifier, { id: s.id, name: s.name });

  const alertThreshold = await db
    .prepare(`SELECT value FROM settings WHERE key = 'telegram_alert_score'`)
    .first<{ value: string }>();
  const alertScore = Number(alertThreshold?.value ?? "9.0") || 9.0;

  let inserted = 0;
  let merged = 0;
  let updated = 0;
  const alerts: Array<{ id: number; title_ru: string | null; ai_score: number | null; url: string; summary_ru: string | null }> = [];
  const skipped: Array<{ url: string; reason: string }> = [];

  for (const it of items) {
    const src = sourceByKey.get(it.source_identifier);
    if (!src) {
      skipped.push({ url: it.url, reason: "unknown_source" });
      continue;
    }

    // 1) существующий по URL
    const existingByUrl = await db
      .prepare(`SELECT id, status, cluster_slug, merged_sources_json FROM items WHERE url = ?`)
      .bind(it.url)
      .first<{ id: number; status: string; cluster_slug: string | null; merged_sources_json: string | null }>();

    if (existingByUrl) {
      // Если это pending_ai — обновляем данными из AI
      if (existingByUrl.status === "pending_ai") {
        await db
          .prepare(
            `UPDATE items SET
               title_ru = ?, summary_ru = ?, key_points_json = ?, category = ?, tags_json = ?,
               ai_score = ?, ai_reason = ?, is_ad = ?, ad_reason = ?, cluster_slug = ?,
               status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
             WHERE id = ?`,
          )
          .bind(
            it.title_ru ?? null,
            it.summary_ru ?? null,
            JSON.stringify(it.key_points ?? []),
            it.category ?? "Общее",
            JSON.stringify(it.tags ?? []),
            it.ai_score ?? null,
            it.ai_reason ?? null,
            it.is_ad ? 1 : 0,
            it.ad_reason ?? null,
            it.cluster_slug ?? null,
            it.status,
            existingByUrl.id,
          )
          .run();
        updated++;
        if (it.status === "inbox" && (it.ai_score ?? 0) >= alertScore) {
          alerts.push({
            id: existingByUrl.id,
            title_ru: it.title_ru ?? null,
            ai_score: it.ai_score ?? null,
            url: it.url,
            summary_ru: it.summary_ru ?? null,
          });
        }
      } else {
        skipped.push({ url: it.url, reason: "duplicate_url" });
      }
      continue;
    }

    // 2) схлопывание сюжета: тот же cluster_slug за 48ч в живых статусах
    if (it.cluster_slug) {
      const sameCluster = await db
        .prepare(
          `SELECT id, merged_sources_json FROM items
            WHERE cluster_slug = ? AND status IN ('inbox','saved','archived')
              AND published_at >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-48 hours')
            ORDER BY published_at DESC LIMIT 1`,
        )
        .bind(it.cluster_slug)
        .first<{ id: number; merged_sources_json: string | null }>();

      if (sameCluster) {
        const mergedList = parseJsonArray<{ name: string; url: string }>(sameCluster.merged_sources_json, []);
        const already = mergedList.some((m) => m.url === it.url);
        if (!already) {
          mergedList.push({ name: src.name, url: it.url });
          await db
            .prepare(
              `UPDATE items SET merged_sources_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?`,
            )
            .bind(JSON.stringify(mergedList), sameCluster.id)
            .run();
        }
        merged++;
        continue;
      }
    }

    // 3) новая запись
    try {
      const res = await db
        .prepare(
          `INSERT INTO items (
             source_id, external_id, url, content_hash, content_type, original_lang,
             title_orig, content_orig, media_url,
             title_ru, summary_ru, key_points_json, category, tags_json,
             ai_score, ai_reason, is_ad, ad_reason, cluster_slug, merged_sources_json,
             status, published_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
        )
        .bind(
          src.id,
          it.external_id ?? null,
          it.url,
          it.content_hash,
          it.content_type,
          it.original_lang ?? "ru",
          it.title_orig,
          it.content_orig,
          it.media_url ?? null,
          it.title_ru ?? null,
          it.summary_ru ?? null,
          JSON.stringify(it.key_points ?? []),
          it.category ?? "Общее",
          JSON.stringify(it.tags ?? []),
          it.ai_score ?? null,
          it.ai_reason ?? null,
          it.is_ad ? 1 : 0,
          it.ad_reason ?? null,
          it.cluster_slug ?? null,
          JSON.stringify(it.merged_sources ?? []),
          it.status,
          it.published_at,
        )
        .run();
      inserted++;

      if (it.status === "inbox" && (it.ai_score ?? 0) >= alertScore) {
        alerts.push({
          id: Number(res.meta?.last_row_id ?? 0),
          title_ru: it.title_ru ?? null,
          ai_score: it.ai_score ?? null,
          url: it.url,
          summary_ru: it.summary_ru ?? null,
        });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      skipped.push({ url: it.url, reason: /UNIQUE/i.test(msg) ? "duplicate_url" : `db_error: ${msg}` });
    }
  }

  return c.json({ ok: true, inserted, updated, merged, skipped, alerts });
});

internal.patch("/sources/status", async (c) => {
  const body = (await c.req.json().catch(() => ({}))) as {
    updates?: Array<{ identifier: string; last_fetched_at?: string; last_error?: string | null }>;
  };
  const updates = Array.isArray(body.updates) ? body.updates : [];
  if (!updates.length) return c.json({ ok: true, updated: 0 });

  const stmt = c.env.DB.prepare(
    `UPDATE sources SET last_fetched_at = ?, last_error = ? WHERE identifier = ?`,
  );
  await c.env.DB.batch(
    updates.map((u) =>
      stmt.bind(u.last_fetched_at ?? new Date().toISOString(), u.last_error ?? null, u.identifier),
    ),
  );
  return c.json({ ok: true, updated: updates.length });
});

internal.get("/feedback/unprocessed", async (c) => {
  const res = await c.env.DB.prepare(
    `SELECT id, title_ru, title_orig, summary_ru, category, tags_json, feedback, feedback_reason, ai_reason
       FROM items
      WHERE feedback != 'none' AND feedback_processed = 0
      ORDER BY updated_at DESC
      LIMIT 200`,
  ).all<{
    id: number;
    title_ru: string | null;
    title_orig: string;
    summary_ru: string | null;
    category: string | null;
    tags_json: string | null;
    feedback: string;
    feedback_reason: string | null;
    ai_reason: string | null;
  }>();

  return c.json({
    feedback: (res.results ?? []).map((r) => ({
      id: r.id,
      title: r.title_ru ?? r.title_orig,
      summary: r.summary_ru,
      category: r.category,
      tags: parseJsonArray<string>(r.tags_json, []),
      feedback: r.feedback,
      feedback_reason: r.feedback_reason,
      ai_reason: r.ai_reason,
    })),
  });
});

internal.post("/profile/update", async (c) => {
  const db = c.env.DB;
  const body = (await c.req.json().catch(() => ({}))) as {
    learned_profile?: string;
    item_ids?: number[];
  };
  const profile = typeof body.learned_profile === "string" ? body.learned_profile : "";
  if (!profile.trim()) return c.json({ error: "empty_profile" }, 400);

  await db
    .prepare(
      `INSERT INTO settings (key, value, updated_at)
       VALUES ('learned_profile', ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
    )
    .bind(profile.trim())
    .run();

  const ids = (body.item_ids ?? []).filter((n) => Number.isFinite(n));
  if (ids.length) {
    const placeholders = ids.map(() => "?").join(",");
    await db
      .prepare(`UPDATE items SET feedback_processed = 1 WHERE id IN (${placeholders})`)
      .bind(...ids)
      .run();
  }

  return c.json({ ok: true, marked: ids.length });
});

internal.post("/runs", async (c) => {
  const body = (await c.req.json().catch(() => ({}))) as {
    started_at?: string;
    finished_at?: string;
    fetched_count?: number;
    ad_blocked_count?: number;
    dedup_skipped_count?: number;
    ai_processed_count?: number;
    inbox_added_count?: number;
    rejected_count?: number;
    pending_ai_count?: number;
    provider_stats?: unknown;
    errors?: unknown;
    status?: "success" | "partial" | "failed";
  };

  const res = await c.env.DB.prepare(
    `INSERT INTO pipeline_runs (
       started_at, finished_at, fetched_count, ad_blocked_count, dedup_skipped_count,
       ai_processed_count, inbox_added_count, rejected_count, pending_ai_count,
       provider_stats_json, errors_json, status
     ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
  )
    .bind(
      body.started_at ?? new Date().toISOString(),
      body.finished_at ?? new Date().toISOString(),
      body.fetched_count ?? 0,
      body.ad_blocked_count ?? 0,
      body.dedup_skipped_count ?? 0,
      body.ai_processed_count ?? 0,
      body.inbox_added_count ?? 0,
      body.rejected_count ?? 0,
      body.pending_ai_count ?? 0,
      JSON.stringify(body.provider_stats ?? {}),
      JSON.stringify(body.errors ?? []),
      body.status ?? "success",
    )
    .run();

  return c.json({ ok: true, id: res.meta?.last_row_id ?? 0 });
});

export default internal;
