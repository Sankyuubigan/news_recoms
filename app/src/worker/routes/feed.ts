import { Hono } from "hono";
import type { Env, FeedItem, ItemRow, ItemStatus, FeedbackValue, SourceType } from "../types";

const feed = new Hono<{ Bindings: Env }>();

function parseJsonArray<T>(raw: string | null | undefined, fallback: T[]): T[] {
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : fallback;
  } catch {
    return fallback;
  }
}

function toFeedItem(row: ItemRow & { source_name?: string; source_type?: SourceType }): FeedItem {
  const { key_points_json, tags_json, merged_sources_json, ...rest } = row;
  return {
    ...rest,
    key_points: parseJsonArray<string>(key_points_json, []),
    tags: parseJsonArray<string>(tags_json, []),
    merged_sources: parseJsonArray<{ name: string; url: string }>(merged_sources_json, []),
    source_name: row.source_name ?? "Источник",
    source_type: row.source_type ?? "rss",
  };
}

const VALID_STATUS: ItemStatus[] = ["pending_ai", "inbox", "saved", "archived", "rejected"];

/** Парсит число с fallback, корректно обрабатывая 0 (в отличие от `x || default`). */
function numOr(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

feed.get("/feed", async (c) => {
  const db = c.env.DB;
  const statusParam = (c.req.query("status") ?? "inbox") as ItemStatus;
  const status = VALID_STATUS.includes(statusParam) && statusParam !== "pending_ai" ? statusParam : "inbox";
  const sort = c.req.query("sort") === "date" ? "date" : "score";
  const category = c.req.query("category")?.trim() ?? "";
  const q = c.req.query("q")?.trim() ?? "";
  const limit = Math.min(Math.max(numOr(c.req.query("limit"), 30), 1), 100);
  const offset = Math.max(numOr(c.req.query("offset"), 0), 0);

  const where: string[] = ["i.status = ?"];
  const params: unknown[] = [status];

  if (category && category !== "Все") {
    where.push("i.category = ?");
    params.push(category);
  }
  if (q) {
    where.push("(i.title_ru LIKE ? OR i.summary_ru LIKE ? OR i.title_orig LIKE ?)");
    const like = `%${q}%`;
    params.push(like, like, like);
  }

  const whereSql = where.join(" AND ");
  const orderSql =
    sort === "date"
      ? "i.published_at DESC"
      : "COALESCE(i.ai_score, 0) DESC, i.published_at DESC";

  const listStmt = db
    .prepare(
      `SELECT i.*, s.name AS source_name, s.type AS source_type
         FROM items i JOIN sources s ON s.id = i.source_id
        WHERE ${whereSql}
        ORDER BY ${orderSql}
        LIMIT ? OFFSET ?`,
    )
    .bind(...params, limit, offset);

  const countStmt = db
    .prepare(`SELECT COUNT(*) AS n FROM items i WHERE ${whereSql}`)
    .bind(...params);

  const countsStmt = db.prepare(
    `SELECT status, COUNT(*) AS n FROM items WHERE status IN ('inbox','saved','archived','rejected') GROUP BY status`,
  );

  const catStmt = db.prepare(
    `SELECT category, COUNT(*) AS n FROM items WHERE status = 'inbox' AND category IS NOT NULL GROUP BY category ORDER BY n DESC LIMIT 20`,
  );

  const [listRes, countRes, countsRes, catRes] = await Promise.all([
    listStmt.all<ItemRow & { source_name: string; source_type: SourceType }>(),
    countStmt.first<{ n: number }>(),
    countsStmt.all<{ status: string; n: number }>(),
    catStmt.all<{ category: string; n: number }>(),
  ]);

  const counts: Record<string, number> = { inbox: 0, saved: 0, archived: 0, rejected: 0 };
  for (const r of countsRes.results ?? []) counts[r.status] = r.n;

  return c.json({
    items: (listRes.results ?? []).map(toFeedItem),
    total: countRes?.n ?? 0,
    counts,
    categories: (catRes.results ?? []).map((r) => r.category).filter(Boolean),
  });
});

feed.patch("/items/:id", async (c) => {
  const db = c.env.DB;
  const id = Number(c.req.param("id"));
  if (!Number.isFinite(id)) return c.json({ error: "bad_id" }, 400);

  const body = (await c.req.json().catch(() => ({}))) as {
    status?: ItemStatus;
    feedback?: FeedbackValue;
    feedback_reason?: string | null;
  };

  const row = await db.prepare(`SELECT status, feedback FROM items WHERE id = ?`).bind(id).first<{
    status: ItemStatus;
    feedback: FeedbackValue;
  }>();
  if (!row) return c.json({ error: "not_found" }, 404);

  const sets: string[] = [];
  const params: unknown[] = [];

  let nextStatus: ItemStatus | undefined = body.status;
  const nextFeedback: FeedbackValue | undefined = body.feedback;

  if (nextStatus && !VALID_STATUS.includes(nextStatus)) {
    return c.json({ error: "bad_status" }, 400);
  }

  // UX-логика: dislike -> архив, если статус явно не задан
  if (nextFeedback === "dislike" && !nextStatus) {
    nextStatus = "archived";
  }
  // UX-логика: лайк из корзины спасает пост обратно в inbox
  if (nextFeedback === "like" && row.status === "rejected" && !nextStatus) {
    nextStatus = "inbox";
  }

  if (nextStatus) {
    sets.push("status = ?");
    params.push(nextStatus);
  }
  if (nextFeedback) {
    sets.push("feedback = ?", "feedback_processed = 0");
    params.push(nextFeedback);
  }
  if (body.feedback_reason !== undefined) {
    sets.push("feedback_reason = ?");
    params.push(body.feedback_reason);
  }

  if (!sets.length) return c.json({ error: "nothing_to_update" }, 400);

  sets.push("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')");
  params.push(id);

  await db.prepare(`UPDATE items SET ${sets.join(", ")} WHERE id = ?`).bind(...params).run();
  const updated = await db.prepare(`SELECT * FROM items WHERE id = ?`).bind(id).first<ItemRow>();
  return c.json({ ok: true, item: updated });
});

feed.post("/items/bulk", async (c) => {
  const db = c.env.DB;
  const body = (await c.req.json().catch(() => ({}))) as {
    action?: string;
    ids?: number[];
    days?: number;
  };
  const action = body.action ?? "";

  if (action === "archive_ids") {
    const ids = (body.ids ?? []).filter((n) => Number.isFinite(n));
    if (!ids.length) return c.json({ ok: true, updated: 0 });
    const placeholders = ids.map(() => "?").join(",");
    const res = await db
      .prepare(
        `UPDATE items SET status = 'archived', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id IN (${placeholders})`,
      )
      .bind(...ids)
      .run();
    return c.json({ ok: true, updated: res.meta?.changes ?? 0 });
  }

  if (action === "archive_older_than") {
    const days = Math.max(numOr(body.days, 3), 0);
    const res = await db
      .prepare(
        `UPDATE items
            SET status = 'archived', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
          WHERE status = 'inbox' AND published_at < strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)`,
      )
      .bind(`-${days} days`)
      .run();
    return c.json({ ok: true, updated: res.meta?.changes ?? 0 });
  }

  if (action === "purge_rejected_older_than") {
    const days = Math.max(numOr(body.days, 30), 1);
    const res = await db
      .prepare(
        `DELETE FROM items
          WHERE status = 'rejected' AND fetched_at < strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)`,
      )
      .bind(`-${days} days`)
      .run();
    return c.json({ ok: true, deleted: res.meta?.changes ?? 0 });
  }

  return c.json({ error: "unknown_action" }, 400);
});

export default feed;
