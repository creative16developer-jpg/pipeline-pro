import { useDashboardStats } from "@/hooks/use-dashboard";
import { Link } from "wouter";
import { Package, Activity, AlertCircle, CheckCircle2, ArrowRight, CloudDownload, Server } from "lucide-react";
import { StatusBadge } from "@/components/StatusBadge";
import { format } from "date-fns";

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
        <div className="flex gap-3">
          <Link href="/sunsky" className="px-4 py-2 rounded-xl bg-secondary text-foreground hover:bg-secondary/80 font-medium transition-colors border border-border flex items-center gap-2 text-sm shadow-sm">
            <CloudDownload className="w-4 h-4" /> Fetch Products
          </Link>
          <Link href="/jobs" className="px-4 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2 text-sm">
            <Activity className="w-4 h-4" /> New Import Job
          </Link>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard title="Total Products" value={stats?.totalProducts || 0} icon={Package} trend="+12%" color="blue" />
        <StatCard title="Pending Process" value={stats?.pendingProducts || 0} icon={Activity} color="amber" />
        <StatCard title="Successfully Uploaded" value={stats?.uploadedProducts || 0} icon={CheckCircle2} color="emerald" />
        <StatCard title="Failed Imports" value={stats?.failedProducts || 0} icon={AlertCircle} color="rose" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Recent Jobs */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-display font-semibold">Recent Jobs</h2>
            <Link href="/jobs" className="text-sm text-primary hover:text-primary/80 flex items-center gap-1">
              View all <ArrowRight className="w-4 h-4" />
            </Link>
          </div>
          
          <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
            {stats?.recentJobs && stats.recentJobs.length > 0 ? (
              <div className="divide-y divide-border/50">
                {stats.recentJobs.map((job) => (
                  <div key={job.id} className="p-4 hover:bg-secondary/20 transition-colors flex items-center justify-between gap-4">
                    <div className="flex items-center gap-4">
                      <div className="w-10 h-10 rounded-full bg-secondary flex items-center justify-center">
                        <Activity className="w-5 h-5 text-primary" />
                      </div>
                      <div>
                        <p className="font-medium text-foreground flex items-center gap-2">
                          Job #{job.id} <StatusBadge status={job.type} />
                        </p>
                        <p className="text-sm text-muted-foreground mt-0.5">
                          {job.createdAt ? format(new Date(job.createdAt), "MMM d, h:mm a") : 'Unknown'}
                        </p>
                      </div>
                    </div>
                    <div className="text-right flex items-center gap-4">
                      <div className="hidden sm:block text-sm">
                        <span className="text-muted-foreground">Progress: </span>
                        <span className="font-medium">{job.progressPercent}%</span>
                      </div>
                      <StatusBadge status={job.status} />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-8 text-center text-muted-foreground flex flex-col items-center">
                <Activity className="w-10 h-10 mb-3 opacity-20" />
                <p>No recent jobs found.</p>
              </div>
            )}
          </div>
        </div>

        {/* System Status */}
        <div className="space-y-4">
          <h2 className="text-xl font-display font-semibold">System Status</h2>
          <div className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5 space-y-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
                  <Activity className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <p className="font-medium">Active Jobs</p>
                  <p className="text-sm text-muted-foreground">Currently running</p>
                </div>
              </div>
              <span className="text-2xl font-bold font-display">{stats?.activeJobs || 0}</span>
            </div>
            
            <div className="w-full h-px bg-border/50" />
            
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-indigo-500/10 flex items-center justify-center">
                  <Server className="w-5 h-5 text-indigo-400" />
                </div>
                <div>
                  <p className="font-medium">Connected Stores</p>
                  <p className="text-sm text-muted-foreground">WooCommerce links</p>
                </div>
              </div>
              <span className="text-2xl font-bold font-display">{stats?.totalStores || 0}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ title, value, icon: Icon, color, trend }: any) {
  const colorMap: Record<string, string> = {
    blue: "text-blue-400 bg-blue-500/10 border-blue-500/20",
    amber: "text-amber-400 bg-amber-500/10 border-amber-500/20",
    emerald: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    rose: "text-rose-400 bg-rose-500/10 border-rose-500/20",
  };

  return (
    <div className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5 relative overflow-hidden group">
      <div className="absolute -right-6 -top-6 w-24 h-24 bg-gradient-to-br from-white/5 to-transparent rounded-full blur-2xl group-hover:scale-150 transition-transform duration-500" />
      <div className="flex items-start justify-between relative z-10">
        <div>
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          <h3 className="text-3xl font-display font-bold text-foreground mt-2">{value.toLocaleString()}</h3>
          {trend && <p className="text-xs text-emerald-400 mt-2 font-medium">{trend} from last week</p>}
        </div>
        <div className={`p-3 rounded-xl border ${colorMap[color]}`}>
          <Icon className="w-5 h-5" />
        </div>
      </div>
    </div>
  );
}
