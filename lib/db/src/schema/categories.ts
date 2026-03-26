import { pgTable, serial, text, timestamp, integer } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";
import { storesTable } from "./stores";

export const wooCategoriesTable = pgTable("woo_categories", {
  id: serial("id").primaryKey(),
  storeId: integer("store_id").notNull().references(() => storesTable.id, { onDelete: "cascade" }),
  wooId: integer("woo_id").notNull(),
  name: text("name").notNull(),
  slug: text("slug").notNull(),
  parentId: integer("parent_id"),
  count: integer("count").notNull().default(0),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const insertWooCategorySchema = createInsertSchema(wooCategoriesTable).omit({ id: true, createdAt: true });
export type InsertWooCategory = z.infer<typeof insertWooCategorySchema>;
export type WooCategory = typeof wooCategoriesTable.$inferSelect;
