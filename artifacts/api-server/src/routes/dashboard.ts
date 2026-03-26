import { Router, type IRouter } from "express";
import { db, productsTable, jobsTable, storesTable } from "@workspace/db";
import { eq, count, desc } from "drizzle-orm";

const router: IRouter = Router();

router.get("/stats", async (req, res) => {
  try {
    const [
      [{ total: totalProducts }],
      [{ total: pendingProducts }],
      [{ total: processedProducts }],
      [{ total: uploadedProducts }],
      [{ total: failedProducts }],
      [{ total: activeJobs }],
      [{ total: totalStores }],
      recentJobs,
    ] = await Promise.all([
      db.select({ total: count() }).from(productsTable),
      db.select({ total: count() }).from(productsTable).where(eq(productsTable.status, "pending")),
      db.select({ total: count() }).from(productsTable).where(eq(productsTable.status, "processed")),
      db.select({ total: count() }).from(productsTable).where(eq(productsTable.status, "uploaded")),
      db.select({ total: count() }).from(productsTable).where(eq(productsTable.status, "failed")),
      db.select({ total: count() }).from(jobsTable).where(eq(jobsTable.status, "running")),
      db.select({ total: count() }).from(storesTable),
      db.select().from(jobsTable).orderBy(desc(jobsTable.createdAt)).limit(5),
    ]);

    res.json({
      totalProducts: Number(totalProducts),
      pendingProducts: Number(pendingProducts),
      processedProducts: Number(processedProducts),
      uploadedProducts: Number(uploadedProducts),
      failedProducts: Number(failedProducts),
      activeJobs: Number(activeJobs),
      totalStores: Number(totalStores),
      recentJobs: recentJobs.map((j) => ({
        ...j,
        startedAt: j.startedAt?.toISOString() ?? null,
        completedAt: j.completedAt?.toISOString() ?? null,
        createdAt: j.createdAt.toISOString(),
      })),
    });
  } catch (err) {
    req.log.error({ err }, "Failed to get dashboard stats");
    res.status(500).json({ error: "Internal server error" });
  }
});

export default router;
