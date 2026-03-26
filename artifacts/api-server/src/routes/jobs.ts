import { Router, type IRouter } from "express";
import { db, jobsTable } from "@workspace/db";
import { eq, count, desc } from "drizzle-orm";
import { z } from "zod";

const router: IRouter = Router();

function formatJob(j: typeof jobsTable.$inferSelect) {
  return {
    ...j,
    startedAt: j.startedAt?.toISOString() ?? null,
    completedAt: j.completedAt?.toISOString() ?? null,
    createdAt: j.createdAt.toISOString(),
  };
}

const createJobBody = z.object({
  type: z.enum(["fetch", "process", "upload", "sync"]),
  storeId: z.number().nullable().optional(),
  config: z.record(z.unknown()).nullable().optional(),
});

router.get("/", async (req, res) => {
  try {
    const page = Math.max(1, parseInt(String(req.query.page || "1")));
    const limit = Math.min(100, Math.max(1, parseInt(String(req.query.limit || "20"))));
    const offset = (page - 1) * limit;

    const [jobs, [{ total }]] = await Promise.all([
      db.select().from(jobsTable).orderBy(desc(jobsTable.createdAt)).limit(limit).offset(offset),
      db.select({ total: count() }).from(jobsTable),
    ]);

    res.json({
      jobs: jobs.map(formatJob),
      total: Number(total),
      page,
      limit,
      totalPages: Math.ceil(Number(total) / limit),
    });
  } catch (err) {
    req.log.error({ err }, "Failed to list jobs");
    res.status(500).json({ error: "Internal server error" });
  }
});

router.post("/", async (req, res) => {
  try {
    const body = createJobBody.parse(req.body);

    const [job] = await db.insert(jobsTable).values({
      type: body.type,
      status: "pending",
      storeId: body.storeId ?? null,
      config: body.config ?? null,
      totalItems: 0,
      processedItems: 0,
      failedItems: 0,
      progressPercent: 0,
    }).returning();

    // Simulate starting the job asynchronously
    void simulateJobExecution(job!.id, body.type);

    res.status(201).json(formatJob(job!));
  } catch (err) {
    req.log.error({ err }, "Failed to create job");
    res.status(400).json({ error: "Invalid input" });
  }
});

router.get("/:id", async (req, res) => {
  try {
    const id = parseInt(req.params.id!);
    const [job] = await db.select().from(jobsTable).where(eq(jobsTable.id, id));
    if (!job) return res.status(404).json({ error: "Job not found" });
    res.json(formatJob(job));
  } catch (err) {
    req.log.error({ err }, "Failed to get job");
    res.status(500).json({ error: "Internal server error" });
  }
});

router.post("/:id/cancel", async (req, res) => {
  try {
    const id = parseInt(req.params.id!);
    const [job] = await db.select().from(jobsTable).where(eq(jobsTable.id, id));
    if (!job) return res.status(404).json({ error: "Job not found" });

    if (!["pending", "running"].includes(job.status)) {
      return res.status(400).json({ error: "Job cannot be cancelled in its current state" });
    }

    const [updated] = await db.update(jobsTable)
      .set({ status: "cancelled", completedAt: new Date() })
      .where(eq(jobsTable.id, id))
      .returning();

    res.json(formatJob(updated!));
  } catch (err) {
    req.log.error({ err }, "Failed to cancel job");
    res.status(500).json({ error: "Internal server error" });
  }
});

async function simulateJobExecution(jobId: number, type: string) {
  await new Promise((r) => setTimeout(r, 500));

  try {
    await db.update(jobsTable).set({
      status: "running",
      startedAt: new Date(),
      totalItems: 50,
    }).where(eq(jobsTable.id, jobId));

    // Check if cancelled
    const checkAndUpdate = async (processed: number) => {
      const [job] = await db.select().from(jobsTable).where(eq(jobsTable.id, jobId));
      if (!job || job.status === "cancelled") return false;

      await db.update(jobsTable).set({
        processedItems: processed,
        progressPercent: (processed / 50) * 100,
      }).where(eq(jobsTable.id, jobId));
      return true;
    };

    for (let i = 10; i <= 50; i += 10) {
      await new Promise((r) => setTimeout(r, 800));
      const cont = await checkAndUpdate(i);
      if (!cont) return;
    }

    await db.update(jobsTable).set({
      status: "completed",
      completedAt: new Date(),
      processedItems: 50,
      progressPercent: 100,
    }).where(eq(jobsTable.id, jobId));
  } catch {
    await db.update(jobsTable).set({
      status: "failed",
      completedAt: new Date(),
      errorMessage: "Simulation error",
    }).where(eq(jobsTable.id, jobId));
  }
}

export default router;
