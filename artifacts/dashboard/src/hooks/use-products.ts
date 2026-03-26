import { useListProducts, useGetProduct, ListProductsParams } from "@workspace/api-client-react";

export function useProducts(params?: ListProductsParams) {
  return useListProducts(params, {
    query: { keepPreviousData: true } as any
  });
}

export function useProduct(id: number) {
  return useGetProduct(id);
}
