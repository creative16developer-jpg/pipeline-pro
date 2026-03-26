import { Router, type IRouter } from "express";
import { db, productsTable } from "@workspace/db";
import { eq, ilike, and, count, sql } from "drizzle-orm";

const router: IRouter = Router();

function formatProduct(p: typeof productsTable.$inferSelect) {
  return {
    ...p,
    lastTestedAt: undefined,
    rawData: p.rawData,
    createdAt: p.createdAt.toISOString(),
    updatedAt: p.updatedAt.toISOString(),
  };
}

router.get("/", async (req, res) => {
  try {
    const page = Math.max(1, parseInt(String(req.query.page || "1")));
    const limit = Math.min(100, Math.max(1, parseInt(String(req.query.limit || "20"))));
    const status = req.query.status as string | undefined;
    const search = req.query.search as string | undefined;
    const offset = (page - 1) * limit;

    const conditions = [];
    if (status && ["pending", "processing", "processed", "uploaded", "failed"].includes(status)) {
      conditions.push(eq(productsTable.status, status as "pending" | "processing" | "processed" | "uploaded" | "failed"));
    }
    if (search) {
      conditions.push(
        sql`(${productsTable.name} ILIKE ${`%${search}%`} OR ${productsTable.sku} ILIKE ${`%${search}%`})`
      );
    }

    const where = conditions.length > 0 ? and(...conditions) : undefined;

    const [products, [{ total }]] = await Promise.all([
      db.select().from(productsTable).where(where).limit(limit).offset(offset).orderBy(productsTable.createdAt),
      db.select({ total: count() }).from(productsTable).where(where),
    ]);

    res.json({
      products: products.map(formatProduct),
      total: Number(total),
      page,
      limit,
      totalPages: Math.ceil(Number(total) / limit),
    });
  } catch (err) {
    req.log.error({ err }, "Failed to list products");
    res.status(500).json({ error: "Internal server error" });
  }
});

router.get("/:id", async (req, res) => {
  try {
    const id = parseInt(req.params.id!);
    const [product] = await db.select().from(productsTable).where(eq(productsTable.id, id));
    if (!product) return res.status(404).json({ error: "Product not found" });
    res.json(formatProduct(product));
  } catch (err) {
    req.log.error({ err }, "Failed to get product");
    res.status(500).json({ error: "Internal server error" });
  }
});

export default router;
