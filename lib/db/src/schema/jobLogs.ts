import { pgTable, serial, text, timestamp, integer, pgEnum } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";
import { jobsTable } from "./jobs";

export const logLevelEnum = pgEnum("log_level", ["info", "warn", "error", "debug"]);

export const jobLogsTable = pgTable("job_logs", {
  id: serial("id").primaryKey(),
  jobId: integer("job_id").notNull().references(() => jobsTable.id, { onDelete: "cascade" }),
  level: logLevelEnum("level").notNull().default("info"),
  message: text("message").notNull(),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const insertJobLogSchema = createInsertSchema(jobLogsTable).omit({ id: true, createdAt: true });
export type InsertJobLog = z.infer<typeof insertJobLogSchema>;
export type JobLog = typeof jobLogsTable.$inferSelect;
