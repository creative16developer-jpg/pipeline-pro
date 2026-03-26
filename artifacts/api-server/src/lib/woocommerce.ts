import { logger } from "./logger";
import type { Store } from "@workspace/db";

export interface WooProduct {
  name: string;
  sku: string;
  description: string;
  regular_price: string;
  status: "draft" | "publish" | "private";
  categories?: { id: number }[];
  images?: { src: string; name?: string; alt?: string }[];
  manage_stock?: boolean;
  stock_status?: "instock" | "outofstock";
  meta_data?: { key: string; value: string }[];
}

export interface WooCategory {
  id: number;
  name: string;
  slug: string;
  parent: number;
  count: number;
}

export interface WooStoreInfo {
  name: string;
  description: string;
  url: string;
  version: string;
}

function buildAuthHeader(store: Store): string {
  const credentials = Buffer.from(`${store.consumerKey}:${store.consumerSecret}`).toString("base64");
  return `Basic ${credentials}`;
}

export async function testWooConnection(store: Store): Promise<{ success: boolean; message: string; storeInfo?: WooStoreInfo }> {
  if (!store.consumerKey || store.consumerKey === "placeholder") {
    return {
      success: false,
      message: "No API credentials configured. Please add your WooCommerce Consumer Key and Secret.",
    };
  }

  try {
    const baseUrl = store.url.replace(/\/$/, "");
    const response = await fetch(`${baseUrl}/wp-json/wc/v3/system_status`, {
      headers: {
        Authorization: buildAuthHeader(store),
        "Content-Type": "application/json",
      },
    });

    if (response.ok) {
      const data = (await response.json()) as { environment?: { store_url?: string; wp_version?: string; wc_version?: string }; store?: { name?: string; description?: string } };
      return {
        success: true,
        message: "Connection successful",
        storeInfo: {
          name: data.store?.name || store.name,
          description: data.store?.description || "",
          url: store.url,
          version: data.environment?.wc_version || "unknown",
        },
      };
    } else {
      return {
        success: false,
        message: `Connection failed: HTTP ${response.status} ${response.statusText}`,
      };
    }
  } catch (err) {
    logger.error({ err, storeId: store.id }, "WooCommerce connection test failed");
    return {
      success: false,
      message: `Connection error: ${err instanceof Error ? err.message : "Unknown error"}`,
    };
  }
}

export async function fetchWooCategories(store: Store): Promise<WooCategory[]> {
  if (!store.consumerKey || store.consumerKey === "placeholder") {
    return [];
  }

  try {
    const baseUrl = store.url.replace(/\/$/, "");
    const allCategories: WooCategory[] = [];
    let page = 1;
    const perPage = 100;

    while (true) {
      const response = await fetch(
        `${baseUrl}/wp-json/wc/v3/products/categories?per_page=${perPage}&page=${page}`,
        {
          headers: { Authorization: buildAuthHeader(store) },
        },
      );

      if (!response.ok) break;
      const categories = (await response.json()) as WooCategory[];
      if (categories.length === 0) break;

      allCategories.push(...categories);
      if (categories.length < perPage) break;
      page++;
    }

    return allCategories;
  } catch (err) {
    logger.error({ err, storeId: store.id }, "Failed to fetch WooCommerce categories");
    return [];
  }
}

export async function createWooProduct(store: Store, product: WooProduct): Promise<{ id: number; link: string } | null> {
  if (!store.consumerKey || store.consumerKey === "placeholder") {
    logger.warn({ storeId: store.id }, "No WooCommerce credentials, skipping product creation");
    return null;
  }

  try {
    const baseUrl = store.url.replace(/\/$/, "");
    const response = await fetch(`${baseUrl}/wp-json/wc/v3/products`, {
      method: "POST",
      headers: {
        Authorization: buildAuthHeader(store),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(product),
    });

    if (!response.ok) {
      const error = (await response.json()) as { message?: string };
      throw new Error(error.message || `HTTP ${response.status}`);
    }

    const result = (await response.json()) as { id: number; permalink: string };
    return { id: result.id, link: result.permalink };
  } catch (err) {
    logger.error({ err, storeId: store.id, sku: product.sku }, "Failed to create WooCommerce product");
    return null;
  }
}
