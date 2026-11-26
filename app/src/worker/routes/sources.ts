import { Hono } from "hono";
import type { Env, SourceRow, SourceType } from "../types";

const sources = new Hono<{ Bindings: Env }>();

const VALID_TYPES: SourceType[] = ["telegram", "rss", "youtube"];

/** Нормализует identifier по типу источника. */
function normalizeIdentifier(type: SourceType, raw: string): string {
  const v = raw.trim();
  if (type === "telegram") {
    return v
      .replace(/^https?:\/\/(www\.)?t\.me\//i, "")
      .replace(/^@/, "")
      .replace(/^s\//i, "")
      .replace(/\/+$/, "")
      .trim();
  }
  if (type === "youtube") {
    // Принимаем channel_id (UC...), ссылку на канал или @handle
    const byChannel = /[?&]channel_id=([A-Za-z0-9_-]+)/.exec(v);
    if (byChannel) return byChannel[1];
    if (/^UC[A-Za-z0-9_-]{20,}$/.test(v)) return v;
    const at = /@([A-Za-z0-9._-]+)/.exec(v);
    if (at) return `@${at[1]}`;
    return v;
  }
  return v;
}

sources.get("/sources", async (c) => {
  const res = await c.env.DB.prepare(`SELECT * FROM sources ORDER BY type, name`).all<SourceRow>();
  return c.json({ sources: res.results ?? [] });
});

sources.post("/sources", async (c) => {
  const body = (await c.req.json().catch(() => ({}))) as {
    type?: SourceType;
    name?: string;
    identifier?: string;
    category_hint?: string;
    fetch_full_text?: boolean;
  };

  const type = body.type;
  if (!type || !VALID_TYPES.includes(type)) return c.json({ error: "bad_type" }, 400);

  const identifier = normalizeIdentifier(type, String(body.identifier ?? ""));
  if (!identifier) return c.json({ error: "empty_identifier" }, 400);

  const name = String(body.name ?? "").trim() || identifier;

  try {
    const res = await c.env.DB.prepare(
      `INSERT INTO sources (type, name, identifier, category_hint, fetch_full_text)
       VALUES (?, ?, ?, ?, ?)`,
    )
      .bind(type, name, identifier, String(body.category_hint ?? ""), body.fetch_full_text ? 1 : 0)
      .run();
    const created = await c.env.DB.prepare(`SELECT * FROM sources WHERE id = ?`)
      .bind(res.meta?.last_row_id)
      .first<SourceRow>();
    return c.json({ ok: true, source: created }, 201);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/UNIQUE/i.test(msg)) return c.json({ error: "duplicate_identifier" }, 409);
    return c.json({ error: "db_error", detail: msg }, 500);
  }
});

sources.patch("/sources/:id", async (c) => {
  const id = Number(c.req.param("id"));
  if (!Number.isFinite(id)) return c.json({ error: "bad_id" }, 400);

  const body = (await c.req.json().catch(() => ({}))) as {
    name?: string;
    identifier?: string;
    category_hint?: string;
    fetch_full_text?: boolean;
    enabled?: boolean;
  };

  const row = await c.env.DB.prepare(`SELECT * FROM sources WHERE id = ?`).bind(id).first<SourceRow>();
  if (!row) return c.json({ error: "not_found" }, 404);

  const sets: string[] = [];
  const params: unknown[] = [];

  if (body.name !== undefined) {
    sets.push("name = ?");
    params.push(String(body.name).trim() || row.name);
  }
  if (body.identifier !== undefined) {
    sets.push("identifier = ?");
    params.push(normalizeIdentifier(row.type, String(body.identifier)));
  }
  if (body.category_hint !== undefined) {
    sets.push("category_hint = ?");
    params.push(String(body.category_hint));
  }
  if (body.fetch_full_text !== undefined) {
    sets.push("fetch_full_text = ?");
    params.push(body.fetch_full_text ? 1 : 0);
  }
  if (body.enabled !== undefined) {
    sets.push("enabled = ?");
    params.push(body.enabled ? 1 : 0);
  }

  if (!sets.length) return c.json({ error: "nothing_to_update" }, 400);
  params.push(id);

  try {
    await c.env.DB.prepare(`UPDATE sources SET ${sets.join(", ")} WHERE id = ?`).bind(...params).run();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/UNIQUE/i.test(msg)) return c.json({ error: "duplicate_identifier" }, 409);
    return c.json({ error: "db_error", detail: msg }, 500);
  }

  const updated = await c.env.DB.prepare(`SELECT * FROM sources WHERE id = ?`).bind(id).first<SourceRow>();
  return c.json({ ok: true, source: updated });
});

sources.delete("/sources/:id", async (c) => {
  const id = Number(c.req.param("id"));
  if (!Number.isFinite(id)) return c.json({ error: "bad_id" }, 400);
  const res = await c.env.DB.prepare(`DELETE FROM sources WHERE id = ?`).bind(id).run();
  return c.json({ ok: true, deleted: res.meta?.changes ?? 0 });
});

export default sources;
