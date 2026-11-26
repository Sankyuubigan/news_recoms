import { Hono } from "hono";
import type { Env } from "./types";
import { requireSession, requireIngest } from "./auth";
import authRoutes from "./routes/auth";
import feedRoutes from "./routes/feed";
import sourceRoutes from "./routes/sources";
import settingsRoutes from "./routes/settings";
import internalRoutes from "./routes/internal";

const app = new Hono<{ Bindings: Env }>();

// --- Public auth endpoints ---
app.route("/api/auth", authRoutes);

// --- Internal pipeline API (Bearer INGEST_SECRET) ---
app.use("/api/internal/*", requireIngest);
app.route("/api/internal", internalRoutes);

// --- User API (session cookie) ---
app.use("/api/v1/*", requireSession);
app.route("/api/v1", feedRoutes);
app.route("/api/v1", sourceRoutes);
app.route("/api/v1", settingsRoutes);

app.all("/api/*", (c) => c.json({ error: "not_found" }, 404));

// --- Static assets (SPA) ---
app.get("*", (c) => {
  if (c.env.ASSETS) return c.env.ASSETS.fetch(c.req.raw);
  return c.text("PulseFilter API is running. Build the client to serve the PWA.", 200);
});

export default app;
