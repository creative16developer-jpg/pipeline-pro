import { useQueryClient } from "@tanstack/react-query";
import {
  useListJobs,
  useCreateJob as useCreateJobGen,
  useGetJob,
  useCancelJob as useCancelJobGen,
  ListJobsParams
} from "@workspace/api-client-react";

export function useJobs(params?: ListJobsParams) {
  return useListJobs(params, {
    query: { keepPreviousData: true, refetchInterval: 5000 } as any
  });
}

export function useJob(id: number) {
  return useGetJob(id, {
    query: { refetchInterval: 3000 } as any
  });
}

export function useCreateJob() {
  const queryClient = useQueryClient();
  return useCreateJobGen({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["/api/jobs"] });
        queryClient.invalidateQueries({ queryKey: ["/api/dashboard/stats"] });
      }
    }
  });
}

export function useCancelJob() {
  const queryClient = useQueryClient();
  return useCancelJobGen({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["/api/jobs"] });
      }
    }
  });
}
