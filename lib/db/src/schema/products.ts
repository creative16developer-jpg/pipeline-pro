import { pgTable, serial, text, timestamp, integer, jsonb, pgEnum } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const productStatusEnum = pgEnum("product_status", ["pending", "processing", "processed", "uploaded", "failed"]);

export const productsTable = pgTable("products", {
  id: serial("id").primaryKey(),
  sunskyId: text("sunsky_id").notNull().unique(),
  sku: text("sku").notNull(),
  name: text("name").notNull(),
  description: text("description"),
  price: text("price"),
  stockStatus: text("stock_status"),
  status: productStatusEnum("status").notNull().default("pending"),
  categoryId: text("category_id"),
  imageCount: integer("image_count").notNull().default(0),
  wooProductId: integer("woo_product_id"),
  errorMessage: text("error_message"),
  rawData: jsonb("raw_data"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
  updatedAt: timestamp("updated_at").notNull().defaultNow(),
});

export const insertProductSchema = createInsertSchema(productsTable).omit({ id: true, createdAt: true, updatedAt: true });
export type InsertProduct = z.infer<typeof insertProductSchema>;
export type Product = typeof productsTable.$inferSelect;
