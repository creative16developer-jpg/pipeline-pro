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

function buildTreeList(items: SunskyCategory[]) {
  const normalized = items.map((item) => ({
    id: String(item.id),
    name: item.name,
    parent_id: item.parent_id ?? item.parentId ?? null,
  }));

  const byId = new Map(normalized.map((item) => [item.id, item]));
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
  if (!ordered.length && byId.size) {
    return Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name));
  }
  return ordered;
}

export function useCategories() {
  const result = useSunskyCategories();
  const raw = result.data;
  const items = Array.isArray(raw)
    ? raw
    : typeof raw === "string"
      ? (() => {
          try {
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
          } catch {
            return [];
          }
        })()
      : [];

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
