import { useQueryClient } from "@tanstack/react-query";
import {
  useSunskyCategories,
  useSunskyFetch as useSunskyFetchGen
} from "@workspace/api-client-react";

export function useCategories() {
  const result = useSunskyCategories();
  const raw = result.data;
  const data = Array.isArray(raw)
    ? raw
    : typeof raw === "string"
    ? (() => { try { const p = JSON.parse(raw); return Array.isArray(p) ? p : []; } catch { return []; } })()
    : [];
  return { ...result, data };
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
