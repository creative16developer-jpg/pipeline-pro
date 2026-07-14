import { useDashboardStats } from "@/hooks/use-dashboard";
import { Link } from "wouter";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  XCircle,
  ArrowRight,
  Zap,
  Store,
} from "lucide-react";
import { StatusBadge } from "@/components/StatusBadge";
import { format } from "date-fns";
import { cn } from "@/lib/utils";

export default function Dashboard() {
  const { data: stats, isLoading } = useDashboardStats();
  const s = stats as any;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full min-h-[400px]">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-display font-bold text-foreground">Dashboard</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            Monitor your WooCommerce import pipelines.
          </p>
        </div>
        <Link href="/pipelines/new">
          <button className="flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 text-sm">
            <Zap className="w-4 h-4" />
            New Pipeline
          </button>
        </Link>
      </div>

      {/* 4 pipeline-focused stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        <StatCard
          title="Active Pipelines"
          value={s?.activePipelines ?? 0}
          icon={Activity}
          color="blue"
          description="Running or queued"
        />
        <StatCard
          title="Waiting for Input"
          value={s?.waitingForInput ?? 0}
          icon={AlertCircle}
          color="amber"
          description="Review paused"
          highlight={(s?.waitingForInput ?? 0) > 0}
        />
        <StatCard
          title="Uploaded (30d)"
          value={s?.uploaded30d ?? s?.uploadedProducts ?? 0}
          icon={CheckCircle2}
          color="emerald"
          description="Products to WooCommerce"
        />
        <StatCard
          title="Failed (30d)"
          value={s?.failed30d ?? s?.failedProducts ?? 0}
          icon={XCircle}
          color="rose"
          description="Need attention"
          highlight={(s?.failed30d ?? 0) > 0}
        />
      </div>

      {/* Recent Pipeline Runs */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-display font-semibold">Recent Pipeline Runs</h2>
          <Link href="/pipelines" className="text-sm text-primary hover:text-primary/80 flex items-center gap-1">
            View all <ArrowRight className="w-4 h-4" />
          </Link>
        </div>

        <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
          {s?.recentPipelineRuns && s.recentPipelineRuns.length > 0 ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-muted-foreground text-xs uppercase tracking-wide">
                  <th className="px-5 py-3 text-left font-medium">#</th>
                  <th className="px-5 py-3 text-left font-medium">Store</th>
                  <th className="px-5 py-3 text-left font-medium">Status</th>
                  <th className="px-5 py-3 text-right font-medium hidden sm:table-cell">Products</th>
                  <th className="px-5 py-3 text-right font-medium hidden md:table-cell">Uploaded</th>
                  <th className="px-5 py-3 text-right font-medium hidden lg:table-cell">Started</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/30">
                {s.recentPipelineRuns.map((run: any) => (
                  <tr
                    key={run.id}
                    className={cn(
                      "hover:bg-secondary/20 transition-colors",
                      run.isWaiting && "bg-amber-500/5 border-l-2 border-amber-500/60"
                    )}
                  >
                    <td className="px-5 py-3.5 text-muted-foreground font-mono text-xs">
                      #{run.id}
                    </td>
                    <td className="px-5 py-3.5">
                      <span className="flex items-center gap-2 font-medium text-foreground">
                        <Store className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                        {run.storeName}
                      </span>
                    </td>
                    <td className="px-5 py-3.5">
                      <PipelineStatusBadge status={run.status} />
                    </td>
                    <td className="px-5 py-3.5 text-right hidden sm:table-cell text-muted-foreground">
                      {run.productsTotal ?? "—"}
                    </td>
                    <td className="px-5 py-3.5 text-right hidden md:table-cell">
                      <span className="text-emerald-400 font-medium">
                        {run.productsUploaded ?? "—"}
                      </span>
                      {(run.productsFailed ?? 0) > 0 && (
                        <span className="text-rose-400 ml-2 text-xs">
                          {run.productsFailed} failed
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3.5 text-right hidden lg:table-cell text-muted-foreground text-xs">
                      {run.createdAt ? format(new Date(run.createdAt), "MMM d, h:mm a") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="p-10 text-center text-muted-foreground flex flex-col items-center gap-3">
              <Activity className="w-10 h-10 opacity-20" />
              <p>No pipeline runs yet.</p>
              <Link href="/pipelines/new">
                <button className="mt-1 flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 text-sm font-medium transition-colors">
                  <Zap className="w-4 h-4" /> Start your first pipeline
                </button>
              </Link>
            </div>
          )}
        </div>
      </div>

      {/* Secondary: stores + active jobs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <MiniStat label="Connected Stores" value={s?.totalStores ?? 0} href="/settings/stores" />
        <MiniStat label="Active Step Jobs" value={s?.activeJobs ?? 0} href="/pipelines" />
      </div>
    </div>
  );
}

function StatCard({
  title,
  value,
  icon: Icon,
  color,
  description,
  highlight,
}: {
  title: string;
  value: number;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  description?: string;
  highlight?: boolean;
}) {
  const colorMap: Record<string, string> = {
    blue:    "text-blue-400 bg-blue-500/10 border-blue-500/20",
    amber:   "text-amber-400 bg-amber-500/10 border-amber-500/20",
    emerald: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    rose:    "text-rose-400 bg-rose-500/10 border-rose-500/20",
  };

  return (
    <div
      className={cn(
        "bg-card border border-border/50 rounded-2xl p-5 shadow-lg shadow-black/5 relative overflow-hidden group",
        highlight && color === "amber" && "border-amber-500/30 ring-1 ring-amber-500/20",
        highlight && color === "rose"  && "border-rose-500/30 ring-1 ring-rose-500/20"
      )}
    >
      <div className="absolute -right-6 -top-6 w-24 h-24 bg-gradient-to-br from-white/5 to-transparent rounded-full blur-2xl group-hover:scale-150 transition-transform duration-500" />
      <div className="flex items-start justify-between relative z-10">
        <div>
          <p className="text-xs font-medium text-muted-foreground">{title}</p>
          <h3 className="text-3xl font-display font-bold text-foreground mt-1.5">
            {value.toLocaleString()}
          </h3>
          {description && (
            <p className="text-xs text-muted-foreground/60 mt-1">{description}</p>
          )}
        </div>
        <div className={cn("p-2.5 rounded-xl border shrink-0", colorMap[color])}>
          <Icon className="w-4 h-4" />
        </div>
      </div>
    </div>
  );
}

function MiniStat({
  label,
  value,
  href,
}: {
  label: string;
  value: number;
  href: string;
}) {
  return (
    <Link href={href}>
      <div className="flex items-center justify-between bg-card border border-border/50 rounded-xl px-5 py-4 hover:border-primary/30 transition-colors cursor-pointer">
        <span className="text-sm text-muted-foreground">{label}</span>
        <span className="text-xl font-display font-bold">{value}</span>
      </div>
    </Link>
  );
}

const STATUS_STYLES: Record<string, string> = {
  queued:          "bg-secondary/60 text-muted-foreground",
  running:         "bg-blue-500/15 text-blue-400",
  review:          "bg-amber-500/15 text-amber-400",
  enrich_review:   "bg-amber-500/15 text-amber-400",
  category_review: "bg-amber-500/15 text-amber-400",
  completed:       "bg-emerald-500/15 text-emerald-400",
  failed:          "bg-rose-500/15 text-rose-400",
  cancelled:       "bg-secondary/60 text-muted-foreground",
};

const STATUS_LABELS: Record<string, string> = {
  queued:          "Queued",
  running:         "Running",
  review:          "Review",
  enrich_review:   "Enrich Review",
  category_review: "Category Review",
  completed:       "Completed",
  failed:          "Failed",
  cancelled:       "Cancelled",
};

function PipelineStatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? "bg-secondary/60 text-muted-foreground";
  const label = STATUS_LABELS[status] ?? status;
  return (
    <span className={cn("px-2 py-0.5 rounded-md text-xs font-medium capitalize", cls)}>
      {label}
    </span>
  );
}
