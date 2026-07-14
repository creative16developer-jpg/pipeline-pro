import { useQueryClient, useMutation } from "@tanstack/react-query";
import {
  useListStores,
  useGetStore,
  useCreateStore as useCreateStoreGen,
  useUpdateStore as useUpdateStoreGen,
  useDeleteStore as useDeleteStoreGen,
  useTestStoreConnection as useTestStoreConnectionGen,
  useListStoreCategories,
  useSyncStoreCategories as useSyncStoreCategoriesGen
} from "@workspace/api-client-react";

export function useStores() {
  return useListStores();
}

export function useStore(id: number) {
  return useGetStore(id);
}

export function useCreateStore() {
  const queryClient = useQueryClient();
  return useCreateStoreGen({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["/api/stores"] });
        queryClient.invalidateQueries({ queryKey: ["/api/dashboard/stats"] });
      }
    }
  });
}

export function useUpdateStore() {
  const queryClient = useQueryClient();
  return useUpdateStoreGen({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: ["/api/stores"] });
        queryClient.invalidateQueries({ queryKey: [`/api/stores/${variables.id}`] });
      }
    }
  });
}

export function useDeleteStore() {
  const queryClient = useQueryClient();
  return useDeleteStoreGen({
    mutation: {
      onSuccess: () => queryClient.invalidateQueries({ queryKey: ["/api/stores"] })
    }
  });
}

export function useTestConnection() {
  const queryClient = useQueryClient();
  return useTestStoreConnectionGen({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: ["/api/stores"] });
        queryClient.invalidateQueries({ queryKey: [`/api/stores/${variables.id}`] });
      }
    }
  });
}

export function useStoreCategories(storeId: number) {
  return useListStoreCategories(storeId);
}

export function useSyncCategories() {
  const queryClient = useQueryClient();
  return useSyncStoreCategoriesGen({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: [`/api/stores/${variables.id}/categories`] });
      }
    }
  });
}

export function usePullFromWooCommerce() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      pullCategories,
      pullAttributes,
    }: {
      id: number;
      pullCategories: boolean;
      pullAttributes: boolean;
    }) => {
      const base = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");
      const res = await fetch(`${base}/api/stores/${id}/pull`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pull_categories: pullCategories,
          pull_attributes: pullAttributes,
        }),
      });
      if (!res.ok) {
        const msg = await res.text().catch(() => res.statusText);
        throw new Error(msg);
      }
      return res.json() as Promise<{
        synced_categories?: number;
        synced_attributes?: number;
        synced_terms?: number;
      }>;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ["/api/stores"] });
      queryClient.invalidateQueries({
        queryKey: [`/api/stores/${variables.id}/categories`],
      });
    },
  });
}
