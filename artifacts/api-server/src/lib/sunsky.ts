import { logger } from "./logger";

const SUNSKY_API_BASE = process.env["SUNSKY_API_URL"] || "https://www.sunsky-online.com/api";
const SUNSKY_API_KEY = process.env["SUNSKY_API_KEY"] || "";
const SUNSKY_API_SECRET = process.env["SUNSKY_API_SECRET"] || "";

export interface SunskyProduct {
  id: string;
  sku: string;
  name: string;
  description: string;
  price: string;
  stockStatus: string;
  categoryId: string;
  images: string[];
  rawData: Record<string, unknown>;
}

export interface SunskyCategory {
  id: string;
  name: string;
  parentId: string | null;
}

export interface SunskyFetchOptions {
  categoryId?: string;
  keyword?: string;
  page?: number;
  limit?: number;
}

function getMockProducts(page: number, limit: number): SunskyProduct[] {
  const categories = ["electronics", "accessories", "gadgets", "toys", "sports"];
  const adjectives = ["Smart", "Premium", "Ultra", "Pro", "Wireless", "Portable", "Digital", "Mini"];
  const nouns = ["Watch", "Speaker", "Earbuds", "Charger", "Stand", "Case", "Light", "Camera"];

  const products: SunskyProduct[] = [];
  const startIndex = (page - 1) * limit;

  for (let i = 0; i < limit; i++) {
    const index = startIndex + i;
    const adj = adjectives[index % adjectives.length];
    const noun = nouns[Math.floor(index / adjectives.length) % nouns.length];
    const cat = categories[index % categories.length];
    const sku = `SK-${String(1000 + index).padStart(6, "0")}`;

    products.push({
      id: `sunsky-${index + 1}`,
      sku,
      name: `${adj} ${noun} ${index + 1}`,
      description: `High-quality ${adj.toLowerCase()} ${noun.toLowerCase()} with advanced features. Perfect for everyday use. Comes with 12-month warranty.`,
      price: ((Math.random() * 100 + 5).toFixed(2)),
      stockStatus: Math.random() > 0.2 ? "in_stock" : "out_of_stock",
      categoryId: cat,
      images: [
        `https://placehold.co/800x800/png?text=${encodeURIComponent(noun)}+1`,
        `https://placehold.co/800x800/png?text=${encodeURIComponent(noun)}+2`,
        `https://placehold.co/800x800/png?text=${encodeURIComponent(noun)}+3`,
      ],
      rawData: {
        source: "sunsky",
        categoryPath: `/${cat}`,
        weight: (Math.random() * 500 + 50).toFixed(0) + "g",
        dimensions: `${Math.floor(Math.random() * 20 + 5)}x${Math.floor(Math.random() * 15 + 3)}x${Math.floor(Math.random() * 10 + 1)}cm`,
        moq: Math.floor(Math.random() * 10 + 1),
      },
    });
  }

  return products;
}

function getMockCategories(): SunskyCategory[] {
  return [
    { id: "electronics", name: "Electronics", parentId: null },
    { id: "accessories", name: "Accessories", parentId: null },
    { id: "gadgets", name: "Gadgets", parentId: "electronics" },
    { id: "toys", name: "Toys & Games", parentId: null },
    { id: "sports", name: "Sports & Outdoors", parentId: null },
    { id: "mobile", name: "Mobile Accessories", parentId: "accessories" },
    { id: "audio", name: "Audio", parentId: "electronics" },
    { id: "wearables", name: "Wearables", parentId: "electronics" },
    { id: "smart-home", name: "Smart Home", parentId: "electronics" },
    { id: "office", name: "Office & Stationery", parentId: null },
  ];
}

export async function fetchSunskyProducts(options: SunskyFetchOptions): Promise<{
  products: SunskyProduct[];
  total: number;
}> {
  const { page = 1, limit = 50, categoryId, keyword } = options;

  if (!SUNSKY_API_KEY) {
    logger.warn("No Sunsky API key configured, returning mock data");
    let products = getMockProducts(page, limit);

    if (categoryId) {
      products = products.filter((p) => p.categoryId === categoryId);
    }
    if (keyword) {
      const kw = keyword.toLowerCase();
      products = products.filter((p) => p.name.toLowerCase().includes(kw) || p.sku.toLowerCase().includes(kw));
    }

    return { products, total: 500 };
  }

  try {
    const params = new URLSearchParams({
      page: String(page),
      pageSize: String(limit),
      ...(categoryId && { cid: categoryId }),
      ...(keyword && { keyword }),
    });

    const response = await fetch(`${SUNSKY_API_BASE}/products?${params}`, {
      headers: {
        Authorization: `Bearer ${SUNSKY_API_KEY}`,
        "X-Api-Secret": SUNSKY_API_SECRET,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      throw new Error(`Sunsky API error: ${response.status} ${response.statusText}`);
    }

    const data = (await response.json()) as { items: SunskyProduct[]; total: number };
    return { products: data.items, total: data.total };
  } catch (err) {
    logger.error({ err }, "Sunsky API fetch failed, falling back to mock data");
    return { products: getMockProducts(page, limit), total: 500 };
  }
}

export async function fetchSunskyCategories(): Promise<SunskyCategory[]> {
  if (!SUNSKY_API_KEY) {
    return getMockCategories();
  }

  try {
    const response = await fetch(`${SUNSKY_API_BASE}/categories`, {
      headers: {
        Authorization: `Bearer ${SUNSKY_API_KEY}`,
        "X-Api-Secret": SUNSKY_API_SECRET,
      },
    });

    if (!response.ok) {
      throw new Error(`Sunsky API error: ${response.status}`);
    }

    return (await response.json()) as SunskyCategory[];
  } catch (err) {
    logger.error({ err }, "Failed to fetch Sunsky categories, using mock");
    return getMockCategories();
  }
}
