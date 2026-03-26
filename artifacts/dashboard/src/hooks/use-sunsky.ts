import { useQueryClient } from "@tanstack/react-query";
import {
  useSunskyCategories,
  useSunskyFetch as useSunskyFetchGen
} from "@workspace/api-client-react";

export function useCategories() {
  return useSunskyCategories();
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
