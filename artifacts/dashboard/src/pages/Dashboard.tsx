import { useDashboardStats, type PipelineRunSummary } from "@/hooks/use-dashboard";
import { Link } from "wouter";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Clock,
  ArrowRight,
  Zap,
  Store,
} from "lucide-react";
import { format } from "date-fns";
import { cn } from "@/lib/utils";

const REVIEW_STATUSES = new Set(["review", "enrich_review", "category_review"]);

function pipelineLabel(status: string): string {
  if (status === "review") return "Waiting for input";
  if (status === "enrich_review") return "Enrich review";
  if (status === "category_review") return "Category review";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function PipelineStatusChip({ status }: { status: string }) {
  const base = "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold whitespace-nowrap";
  if (status === "running") return <span className={cn(base, "bg-blue-500/15 text-blue-400 border border-blue-500/20")}>Running</span>;
  if (REVIEW_STATUSES.has(status)) return <span className={cn(base, "bg-amber-500/15 text-amber-400 border border-amber-500/20")}>{pipelineLabel(status)}</span>;
  if (status === "completed") return <span className={cn(base, "bg-emerald-500/15 text-emerald-400 border border-emerald-500/20")}>Completed</span>;
  if (status === "failed") return <span className={cn(base, "bg-rose-500/15 text-rose-400 border border-rose-500/20")}>Failed</span>;
  if (status === "cancelled") return <span className={cn(base, "bg-zinc-500/15 text-zinc-400 border border-zinc-500/20")}>Cancelled</span>;
  if (status === "queued") return <span className={cn(base, "bg-zinc-500/15 text-zinc-400 border border-zinc-500/20")}>Queued</span>;
  return <span className={cn(base, "bg-secondary text-muted-foreground")}>{status}</span>;
}

function PipelineRow({ run }: { run: PipelineRunSummary }) {
  const isWaiting = REVIEW_STATUSES.has(run.status);

  return (
    <div className={cn(
      "p-4 hover:bg-secondary/20 transition-colors flex items-center justify-between gap-4",
      isWaiting && "bg-amber-500/5 border-l-2 border-amber-500/40"
    )}>
      <div className="flex items-center gap-4 min-w-0">
        <div className={cn(
          "w-10 h-10 rounded-full flex items-center justify-center shrink-0",
          isWaiting ? "bg-amber-500/10" : "bg-secondary"
        )}>
          <Activity className={cn("w-5 h-5", isWaiting ? "text-amber-400" : "text-primary")} />
        </div>
        <div className="min-w-0">
          <p className="font-medium text-foreground flex items-center gap-2 flex-wrap">
            <span>PL-{String(run.id).padStart(3, "0")}</span>
            {run.currentStep && (
              <span className="text-xs text-muted-foreground font-normal">
                step: {run.currentStep}
              </span>
            )}
          </p>
          <p className="text-sm text-muted-foreground mt-0.5">
            {run.createdAt ? format(new Date(run.createdAt), "MMM d, h:mm a") : "—"}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-3 shrink-0">
        <PipelineStatusChip status={run.status} />
        {isWaiting && (
          <Link href={`/pipelines/${run.id}`}>
            <button className="px-3 py-1.5 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 text-xs font-semibold border border-amber-500/20 transition-colors flex items-center gap-1">
              Review <ArrowRight className="w-3 h-3" />
            </button>
          </Link>
        )}
        {run.status === "running" && (
          <Link href={`/pipelines/${run.id}`}>
            <button className="px-3 py-1.5 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary text-xs font-semibold border border-primary/20 transition-colors flex items-center gap-1">
              View <ArrowRight className="w-3 h-3" />
            </button>
          </Link>
        )}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { data: stats, isLoading } = useDashboardStats();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full min-h-[400px]">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin"></div>
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
        <Link href="/pipelines">
          <button className="px-4 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2 text-sm">
            <Zap className="w-4 h-4" /> Run Pipeline
          </button>
        </Link>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Active Pipelines"
          value={stats?.activePipelines ?? 0}
          icon={Activity}
          color="blue"
        />
        <StatCard
          title="Waiting for Input"
          value={stats?.waitingForInput ?? 0}
          icon={Clock}
          color="amber"
        />
        <StatCard
          title="Uploaded (30d)"
          value={stats?.uploaded30d ?? 0}
          icon={CheckCircle2}
          color="emerald"
        />
        <StatCard
          title="Failed (30d)"
          value={stats?.failed30d ?? 0}
          icon={AlertCircle}
          color="rose"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Recent Pipeline Runs */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-display font-semibold">Recent Runs</h2>
            <Link href="/pipelines" className="text-sm text-primary hover:text-primary/80 flex items-center gap-1">
              View all <ArrowRight className="w-4 h-4" />
            </Link>
          </div>

          <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
            {stats?.recentPipelines && stats.recentPipelines.length > 0 ? (
              <div className="divide-y divide-border/50">
                {stats.recentPipelines.map((run) => (
                  <PipelineRow key={run.id} run={run} />
                ))}
              </div>
            ) : (
              <div className="p-8 text-center text-muted-foreground flex flex-col items-center">
                <Activity className="w-10 h-10 mb-3 opacity-20" />
                <p>No pipeline runs yet.</p>
                <Link href="/pipelines">
                  <button className="mt-4 px-4 py-2 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary text-sm font-medium transition-colors">
                    Start your first pipeline
                  </button>
                </Link>
              </div>
            )}
          </div>
        </div>

        {/* Summary panel */}
        <div className="space-y-4">
          <h2 className="text-xl font-display font-semibold">Summary</h2>
          <div className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5 space-y-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
                  <Activity className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <p className="font-medium">Active</p>
                  <p className="text-sm text-muted-foreground">Pipelines running</p>
                </div>
              </div>
              <span className="text-2xl font-bold font-display">{stats?.activePipelines ?? 0}</span>
            </div>

            <div className="w-full h-px bg-border/50" />

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-amber-500/10 flex items-center justify-center">
                  <Clock className="w-5 h-5 text-amber-400" />
                </div>
                <div>
                  <p className="font-medium">Awaiting review</p>
                  <p className="text-sm text-muted-foreground">Need your input</p>
                </div>
              </div>
              <span className="text-2xl font-bold font-display text-amber-400">{stats?.waitingForInput ?? 0}</span>
            </div>

            <div className="w-full h-px bg-border/50" />

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-indigo-500/10 flex items-center justify-center">
                  <Store className="w-5 h-5 text-indigo-400" />
                </div>
                <div>
                  <p className="font-medium">Connected stores</p>
                  <p className="text-sm text-muted-foreground">WooCommerce links</p>
                </div>
              </div>
              <span className="text-2xl font-bold font-display">{stats?.totalStores ?? 0}</span>
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
                      {stats!.waitingForInput} pipeline{stats!.waitingForInput > 1 ? "s" : ""} waiting
                    </p>
                  </div>
                </div>
                <ArrowRight className="w-4 h-4 text-amber-400" />
              </div>
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({ title, value, icon: Icon, color }: {
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
