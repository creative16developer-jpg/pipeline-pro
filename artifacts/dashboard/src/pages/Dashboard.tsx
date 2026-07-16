import { useDashboardStats, type PipelineRunSummary } from "@/hooks/use-dashboard";
import { useStores } from "@/hooks/use-stores";
import { Link } from "wouter";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Clock,
  ArrowRight,
  Zap,
  Eye,
  RotateCcw,
  CheckCircle,
} from "lucide-react";
import { format } from "date-fns";
import { cn } from "@/lib/utils";
import { getStoreColor } from "@/lib/store-colors";

const REVIEW_STATUSES = new Set(["review", "enrich_review", "category_review"]);

function pipelineLabel(status: string): string {
  if (status === "review") return "Cat. Review";
  if (status === "enrich_review") return "Attr. Review";
  if (status === "category_review") return "Cat. Review";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function PipelineStatusChip({ status }: { status: string }) {
  const base = "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold whitespace-nowrap";
  if (status === "running")
    return (
      <span className={cn(base, "bg-blue-500/15 text-blue-400 border border-blue-500/20")}>
        <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
        Running
      </span>
    );
  if (REVIEW_STATUSES.has(status))
    return (
      <span className={cn(base, "bg-amber-500/15 text-amber-400 border border-amber-500/20")}>
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
        {pipelineLabel(status)}
      </span>
    );
  if (status === "completed")
    return (
      <span className={cn(base, "bg-emerald-500/15 text-emerald-400 border border-emerald-500/20")}>
        <CheckCircle className="w-3 h-3" />
        Completed
      </span>
    );
  if (status === "failed")
    return (
      <span className={cn(base, "bg-rose-500/15 text-rose-400 border border-rose-500/20")}>
        <AlertCircle className="w-3 h-3" />
        Failed
      </span>
    );
  if (status === "cancelled")
    return <span className={cn(base, "bg-zinc-500/15 text-zinc-400 border border-zinc-500/20")}>Cancelled</span>;
  if (status === "queued")
    return <span className={cn(base, "bg-zinc-500/15 text-zinc-400 border border-zinc-500/20")}>Queued</span>;
  return <span className={cn(base, "bg-secondary text-muted-foreground")}>{status}</span>;
}

function StoreBadge({ storeId, storeName }: { storeId: number; storeName?: string }) {
  const c = getStoreColor(storeId);
  return (
    <span className={cn("inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium border", c.bg, c.text, c.border)}>
      <span className={cn("w-1.5 h-1.5 rounded-full", c.dot)} />
      {storeName ?? `Store #${storeId}`}
    </span>
  );
}

function PipelineTableRow({ run, storeName }: { run: PipelineRunSummary; storeName?: string }) {
  const isWaiting = REVIEW_STATUSES.has(run.status);

  return (
    <tr className={cn(
      "border-b border-border/40 hover:bg-secondary/10 transition-colors",
      isWaiting && "bg-amber-500/5"
    )}>
      <td className="px-4 py-3 font-mono text-sm font-semibold">
        PL-{String(run.id).padStart(3, "0")}
      </td>
      <td className="px-4 py-3">
        <StoreBadge storeId={run.storeId} storeName={storeName} />
      </td>
      <td className="px-4 py-3">
        <PipelineStatusChip status={run.status} />
      </td>
      <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
        {run.createdAt ? format(new Date(run.createdAt), "MMM d, HH:mm") : "—"}
      </td>
      <td className="px-4 py-3 text-right">
        <div className="flex items-center gap-2 justify-end">
          {(run.status === "running" || run.status === "queued") && (
            <Link href={`/pipelines/${run.id}`}>
              <button className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary text-xs font-medium border border-primary/20 transition-colors">
                <Eye className="w-3 h-3" /> View
              </button>
            </Link>
          )}
          {isWaiting && (
            <Link href={`/pipelines/${run.id}`}>
              <button className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 text-xs font-medium border border-amber-500/20 transition-colors">
                Review <ArrowRight className="w-3 h-3" />
              </button>
            </Link>
          )}
          {(run.status === "failed" || run.status === "cancelled") && (
            <Link href={`/pipelines/${run.id}`}>
              <button className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-secondary hover:bg-secondary/80 text-muted-foreground text-xs font-medium transition-colors">
                <RotateCcw className="w-3 h-3" /> Retry
              </button>
            </Link>
          )}
          {run.status === "completed" && (
            <Link href={`/pipelines/${run.id}`}>
              <button className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-secondary hover:bg-secondary/80 text-muted-foreground text-xs font-medium transition-colors">
                <Eye className="w-3 h-3" /> View
              </button>
            </Link>
          )}
        </div>
      </td>
    </tr>
  );
}

export default function Dashboard() {
  const { data: stats, isLoading } = useDashboardStats();
  const { data: stores } = useStores();
  const storeMap = Object.fromEntries((stores ?? []).map((s) => [s.id, s.name]));

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full min-h-[400px]">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground">Overview</h1>
          <p className="text-muted-foreground mt-1">Monitor your WooCommerce import pipeline.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            System healthy
          </span>
          <Link href="/pipeline">
            <button className="px-4 py-2.5 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2 text-sm">
              <Zap className="w-4 h-4" /> New Pipeline
            </button>
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard title="Active Pipelines" value={stats?.activePipelines ?? 0} icon={Activity} color="blue" />
        <StatCard title="Waiting for Input" value={stats?.waitingForInput ?? 0} icon={Clock} color="amber" />
        <StatCard title="Uploaded (30d)" value={stats?.uploaded30d ?? 0} icon={CheckCircle2} color="emerald" />
        <StatCard title="Failed (30d)" value={stats?.failed30d ?? 0} icon={AlertCircle} color="rose" />
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-display font-semibold">Recent Pipeline Runs</h2>
          <Link href="/pipelines" className="text-sm text-primary hover:text-primary/80 flex items-center gap-1">
            View all <ArrowRight className="w-4 h-4" />
          </Link>
        </div>

        <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
          {stats?.recentPipelines && stats.recentPipelines.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-border/50 bg-secondary/30 text-xs text-muted-foreground uppercase tracking-wider">
                    <th className="px-4 py-3 font-medium">Pipeline</th>
                    <th className="px-4 py-3 font-medium">Store</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Started</th>
                    <th className="px-4 py-3 font-medium text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.recentPipelines.map((run) => (
                    <PipelineTableRow key={run.id} run={run} storeName={storeMap[run.storeId]} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-10 text-center text-muted-foreground flex flex-col items-center">
              <Activity className="w-10 h-10 mb-3 opacity-20" />
              <p className="font-medium">No pipeline runs yet</p>
              <p className="text-sm mt-1 mb-4">Start your first pipeline to import products from Sunsky.</p>
              <Link href="/pipeline">
                <button className="px-4 py-2 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary text-sm font-medium transition-colors border border-primary/20">
                  ⚡ Start first pipeline
                </button>
              </Link>
            </div>
          )}
        </div>
      </div>

      {(stats?.waitingForInput ?? 0) > 0 && (
        <Link href="/pipelines">
          <div className="bg-amber-500/10 border border-amber-500/20 rounded-2xl p-4 flex items-center justify-between cursor-pointer hover:bg-amber-500/15 transition-colors">
            <div className="flex items-center gap-3">
              <Clock className="w-5 h-5 text-amber-400" />
              <div>
                <p className="font-medium text-amber-300 text-sm">Action required</p>
                <p className="text-xs text-amber-400/70">
                  {stats!.waitingForInput} pipeline{stats!.waitingForInput > 1 ? "s" : ""} waiting for review
                </p>
              </div>
            </div>
            <ArrowRight className="w-4 h-4 text-amber-400" />
          </div>
        </Link>
      )}
    </div>
  );
}

function StatCard({
  title,
  value,
  icon: Icon,
  color,
}: {
  title: string;
  value: number;
  icon: React.ElementType;
  color: "blue" | "amber" | "emerald" | "rose";
}) {
  const colorMap = {
    blue: "text-blue-400 bg-blue-500/10 border-blue-500/20",
    amber: "text-amber-400 bg-amber-500/10 border-amber-500/20",
    emerald: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    rose: "text-rose-400 bg-rose-500/10 border-rose-500/20",
  } as const;

  return (
    <div className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5 relative overflow-hidden group">
      <div className="absolute -right-6 -top-6 w-24 h-24 bg-gradient-to-br from-white/5 to-transparent rounded-full blur-2xl group-hover:scale-150 transition-transform duration-500" />
      <div className="flex items-start justify-between relative z-10">
        <div>
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          <h3 className="text-3xl font-display font-bold text-foreground mt-2">{value.toLocaleString()}</h3>
        </div>
        <div className={`p-3 rounded-xl border ${colorMap[color]}`}>
          <Icon className="w-5 h-5" />
        </div>
      </div>
    </div>
  );
}
