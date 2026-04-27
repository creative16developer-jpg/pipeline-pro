import { useQueryClient } from "@tanstack/react-query";
import {
  useSunskyCategories,
  useSunskyFetch as useSunskyFetchGen
} from "@workspace/api-client-react";

type SunskyCategory = {
  id: string;
  name: string;
  parent_id?: string | null;
  parentId?: string | null;
};

function normalizeList(raw: unknown): SunskyCategory[] {
  const items = Array.isArray(raw) ? raw : [];
  return items
    .map((item: any) => ({
      id: String(item.id ?? item.categoryId ?? ""),
      name: String(item.name ?? item.title ?? ""),
      parent_id: item.parent_id ?? item.parentId ?? null,
    }))
    .filter((item) => item.id && item.name);
}

function buildTreeList(items: SunskyCategory[]) {
  const normalized = items.map((item) => ({
    id: String(item.id),
    name: item.name,
    parent_id: item.parent_id ?? item.parentId ?? null,
  }));

  const children = new Map<string | null, SunskyCategory[]>();
  for (const item of normalized) {
    const parent = item.parent_id ?? null;
    const list = children.get(parent) ?? [];
    list.push(item);
    children.set(parent, list);
  }

  const ordered: SunskyCategory[] = [];
  const walk = (parent: string | null, depth: number) => {
    const list = (children.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name));
    for (const item of list) {
      ordered.push({ ...item, name: `${"— ".repeat(depth)}${item.name}` });
      walk(item.id, depth + 1);
    }
  };

  walk(null, 0);
  return ordered.length ? ordered : normalized.sort((a, b) => a.name.localeCompare(b.name));
}

export function useCategories() {
  const result = useSunskyCategories();
  const raw = result.data;
  const items = normalizeList(raw);
  return { ...result, data: buildTreeList(items) };
}

export function useSunskyFetch() {
  const queryClient = useQueryClient();
  return useSunskyFetchGen({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["/api/products"] });
        queryClient.invalidateQueries({ queryKey: ["/api/jobs"] });
        queryClient.invalidateQueries({ queryKey: ["/api/dashboard/stats"] });
      }
    }
  });
}
