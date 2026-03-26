import { Router, type IRouter } from "express";
import { db, productsTable, jobsTable } from "@workspace/db";
import { fetchSunskyProducts, fetchSunskyCategories } from "../lib/sunsky";
import { eq } from "drizzle-orm";
import { z } from "zod";

const router: IRouter = Router();

const fetchBody = z.object({
  categoryId: z.string().nullable().optional(),
  keyword: z.string().nullable().optional(),
  page: z.number().int().positive().default(1),
  limit: z.number().int().min(1).max(200).default(50),
});

router.post("/fetch", async (req, res) => {
  try {
    const body = fetchBody.parse(req.body);

    // Create a job record
    const [job] = await db.insert(jobsTable).values({
      type: "fetch",
      status: "running",
      startedAt: new Date(),
      totalItems: 0,
      processedItems: 0,
      failedItems: 0,
      progressPercent: 0,
      config: body as Record<string, unknown>,
    }).returning();

    const { products, total } = await fetchSunskyProducts({
      categoryId: body.categoryId ?? undefined,
      keyword: body.keyword ?? undefined,
      page: body.page,
      limit: body.limit,
    });

    let saved = 0;
    let skipped = 0;

    for (const product of products) {
      try {
        await db.insert(productsTable).values({
          sunskyId: product.id,
          sku: product.sku,
          name: product.name,
          description: product.description,
          price: product.price,
          stockStatus: product.stockStatus,
          categoryId: product.categoryId,
          imageCount: product.images.length,
          status: "pending",
          rawData: product.rawData as Record<string, unknown>,
        }).onConflictDoUpdate({
          target: productsTable.sunskyId,
          set: {
            name: product.name,
            description: product.description,
            price: product.price,
            stockStatus: product.stockStatus,
            imageCount: product.images.length,
            updatedAt: new Date(),
          },
        });
        saved++;
      } catch {
        skipped++;
      }
    }

    await db.update(jobsTable).set({
      status: "completed",
      completedAt: new Date(),
      totalItems: products.length,
      processedItems: saved,
      failedItems: skipped,
      progressPercent: 100,
    }).where(eq(jobsTable.id, job!.id));

    res.json({
      fetched: products.length,
      saved,
      skipped,
      jobId: job!.id,
    });
  } catch (err) {
    req.log.error({ err }, "Sunsky fetch failed");
    res.status(500).json({ error: "Fetch failed" });
  }
});

router.get("/categories", async (_req, res) => {
  try {
    const categories = await fetchSunskyCategories();
    res.json(categories);
  } catch (err) {
    res.status(500).json({ error: "Failed to fetch categories" });
  }
});

export default router;
