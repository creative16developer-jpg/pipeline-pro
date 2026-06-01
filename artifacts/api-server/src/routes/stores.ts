import { Router, type IRouter, type Request, type Response } from "express";
import { db, storesTable, wooCategoriesTable } from "@workspace/db";
import { eq } from "drizzle-orm";
import { testWooConnection, fetchWooCategories } from "../lib/woocommerce";
import { z } from "zod";
import http from "node:http";

const PYTHON_HOST = "127.0.0.1";
const PYTHON_PORT = 8000;

function proxyToPython(req: Request, res: Response, basePath: string) {
  const qs = req.url.includes("?") ? "?" + req.url.split("?")[1] : "";
  const target = `${basePath}${req.path === "/" ? "" : req.path}${qs}`;
  const options: http.RequestOptions = {
    hostname: PYTHON_HOST,
    port: PYTHON_PORT,
    path: target,
    method: req.method,
    headers: { ...req.headers, host: `${PYTHON_HOST}:${PYTHON_PORT}` },
  };
  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
    proxyRes.pipe(res, { end: true });
  });
  proxyReq.on("error", () => {
    if (!res.headersSent) res.status(502).json({ error: "Python service unavailable" });
  });
  if (req.body && ["POST", "PUT", "PATCH"].includes(req.method)) {
    const body = JSON.stringify(req.body);
    proxyReq.setHeader("Content-Type", "application/json");
    proxyReq.setHeader("Content-Length", Buffer.byteLength(body));
    proxyReq.write(body);
  }
  proxyReq.end();
}

const router: IRouter = Router();

const createStoreBody = z.object({
  name: z.string().min(1),
  url: z.string().url(),
  consumerKey: z.string().min(1),
  consumerSecret: z.string().min(1),
});

router.get("/", async (req, res) => {
  try {
    const stores = await db.select().from(storesTable).orderBy(storesTable.createdAt);
    const masked = stores.map((s) => ({
      ...s,
      consumerKey: s.consumerKey.slice(0, 8) + "...",
      consumerSecret: s.consumerSecret.slice(0, 8) + "...",
      lastTestedAt: s.lastTestedAt?.toISOString() ?? null,
      createdAt: s.createdAt.toISOString(),
    }));
    res.json(masked);
  } catch (err) {
    req.log.error({ err }, "Failed to list stores");
    res.status(500).json({ error: "Internal server error" });
  }
});

router.post("/", async (req, res) => {
  try {
    const body = createStoreBody.parse(req.body);
    const [store] = await db.insert(storesTable).values({
      ...body,
      status: "inactive",
    }).returning();
    res.status(201).json({
      ...store,
      lastTestedAt: null,
      createdAt: store!.createdAt.toISOString(),
    });
  } catch (err) {
    req.log.error({ err }, "Failed to create store");
    res.status(400).json({ error: "Invalid input" });
  }
});

router.get("/:id", async (req, res) => {
  try {
    const id = parseInt(req.params.id!);
    const [store] = await db.select().from(storesTable).where(eq(storesTable.id, id));
    if (!store) return res.status(404).json({ error: "Store not found" });
    res.json({
      ...store,
      lastTestedAt: store.lastTestedAt?.toISOString() ?? null,
      createdAt: store.createdAt.toISOString(),
    });
  } catch (err) {
    req.log.error({ err }, "Failed to get store");
    res.status(500).json({ error: "Internal server error" });
  }
});

router.put("/:id", async (req, res) => {
  try {
    const id = parseInt(req.params.id!);
    const body = createStoreBody.parse(req.body);
    const [updated] = await db.update(storesTable)
      .set({ ...body, updatedAt: new Date() })
      .where(eq(storesTable.id, id))
      .returning();
    if (!updated) return res.status(404).json({ error: "Store not found" });
    res.json({
      ...updated,
      lastTestedAt: updated.lastTestedAt?.toISOString() ?? null,
      createdAt: updated.createdAt.toISOString(),
    });
  } catch (err) {
    req.log.error({ err }, "Failed to update store");
    res.status(400).json({ error: "Invalid input" });
  }
});

router.delete("/:id", async (req, res) => {
  try {
    const id = parseInt(req.params.id!);
    await db.delete(storesTable).where(eq(storesTable.id, id));
    res.status(204).send();
  } catch (err) {
    req.log.error({ err }, "Failed to delete store");
    res.status(500).json({ error: "Internal server error" });
  }
});

router.post("/:id/test", async (req, res) => {
  try {
    const id = parseInt(req.params.id!);
    const [store] = await db.select().from(storesTable).where(eq(storesTable.id, id));
    if (!store) return res.status(404).json({ error: "Store not found" });

    const result = await testWooConnection(store);
    const newStatus = result.success ? "active" : "error";

    await db.update(storesTable)
      .set({ status: newStatus, lastTestedAt: new Date(), updatedAt: new Date() })
      .where(eq(storesTable.id, id));

    res.json(result);
  } catch (err) {
    req.log.error({ err }, "Failed to test store connection");
    res.status(500).json({ error: "Internal server error" });
  }
});

router.get("/:id/categories", async (req, res) => {
  try {
    const storeId = parseInt(req.params.id!);
    const categories = await db.select().from(wooCategoriesTable).where(eq(wooCategoriesTable.storeId, storeId));
    const result = categories.map((c) => ({
      id: c.id,
      wooId: c.wooId,
      name: c.name,
      slug: c.slug,
      parentId: c.parentId,
      count: c.count,
    }));
    res.json(result);
  } catch (err) {
    req.log.error({ err }, "Failed to list categories");
    res.status(500).json({ error: "Internal server error" });
  }
});

router.post("/:id/categories", async (req, res) => {
  try {
    const storeId = parseInt(req.params.id!);
    const [store] = await db.select().from(storesTable).where(eq(storesTable.id, storeId));
    if (!store) return res.status(404).json({ error: "Store not found" });

    const wooCategories = await fetchWooCategories(store);

    if (wooCategories.length === 0) {
      return res.json({ synced: 0, created: 0, updated: 0 });
    }

    await db.delete(wooCategoriesTable).where(eq(wooCategoriesTable.storeId, storeId));

    await db.insert(wooCategoriesTable).values(
      wooCategories.map((c) => ({
        storeId,
        wooId: c.id,
        name: c.name,
        slug: c.slug,
        parentId: c.parent || null,
        count: c.count,
      })),
    );

    res.json({ synced: wooCategories.length, created: wooCategories.length, updated: 0 });
  } catch (err) {
    req.log.error({ err }, "Failed to sync categories");
    res.status(500).json({ error: "Internal server error" });
  }
});

// ── Proxy any unhandled /stores/* paths to Python (category-mappings, normalisation-dict, etc.)
router.all("/{*path}", (req, res) => {
  proxyToPython(req, res, "/api/stores");
});

export default router;
