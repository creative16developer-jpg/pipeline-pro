import { Router, type IRouter } from "express";
import { db, storesTable, wooCategoriesTable } from "@workspace/db";
import { eq } from "drizzle-orm";
import { testWooConnection, fetchWooCategories } from "../lib/woocommerce";
import { z } from "zod";

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

export default router;
