import { useQuery } from "@tanstack/react-query";

export interface PipelineRunSummary {
  id: number;
  storeId: number;
  fetchJobId: number;
  status: string;
  currentStep?: string | null;
  errorMessage?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DashboardStats {
  activePipelines: number;
  waitingForInput: number;
  uploaded30d: number;
  failed30d: number;
  totalStores: number;
  recentPipelines: PipelineRunSummary[];
}

async function fetchDashboardStats(): Promise<DashboardStats> {
  const res = await fetch("/api/dashboard/stats");
  if (!res.ok) throw new Error("Failed to fetch dashboard stats");
  return res.json();
}

export function useDashboardStats() {
  return useQuery<DashboardStats>({
    queryKey: ["dashboard-stats"],
    queryFn: fetchDashboardStats,
    refetchInterval: 15_000,
  });
}
