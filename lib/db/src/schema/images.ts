import { pgTable, serial, text, timestamp, integer, boolean, pgEnum } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";
import { productsTable } from "./products";

export const imageStatusEnum = pgEnum("image_status", ["pending", "downloaded", "compressed", "watermarked", "uploaded", "failed"]);

export const imagesTable = pgTable("images", {
  id: serial("id").primaryKey(),
  productId: integer("product_id").notNull().references(() => productsTable.id, { onDelete: "cascade" }),
  originalUrl: text("original_url").notNull(),
  localPath: text("local_path"),
  processedPath: text("processed_path"),
  wooImageId: integer("woo_image_id"),
  position: integer("position").notNull().default(0),
  status: imageStatusEnum("status").notNull().default("pending"),
  isMain: boolean("is_main").notNull().default(false),
  errorMessage: text("error_message"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
  updatedAt: timestamp("updated_at").notNull().defaultNow(),
});

export const insertImageSchema = createInsertSchema(imagesTable).omit({ id: true, createdAt: true, updatedAt: true });
export type InsertImage = z.infer<typeof insertImageSchema>;
export type Image = typeof imagesTable.$inferSelect;
