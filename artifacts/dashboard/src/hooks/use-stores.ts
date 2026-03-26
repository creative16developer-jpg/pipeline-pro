import { useQueryClient } from "@tanstack/react-query";
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
