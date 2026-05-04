import { useState, useEffect, useRef, useCallback } from "react";
import { useLocation } from "wouter";
import {
  Plus, ChevronDown, ChevronRight, CheckCircle2, XCircle,
  Loader2, Clock, AlertTriangle, Square, Play, RefreshCw,
  RotateCcw, Eye, Info, ChevronUp, Activity
} from "lucide-react";
import { useStores } from "@/hooks/use-stores";
import { useToast } from "@/hooks/use-toast";
import { getStoreColor } from "@/lib/store-colors";
import { cn } from "@/lib/utils";
import { format } from "date-fns";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface StepJob {
  id: number;
  type: string;
  status: string;
  total_items: number;
  processed_items: number;
  failed_items: number;
  progress_percent: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}

interface Pipeline {
  id: number;
  pl_id: string;
  store_id: number;
  fetch_job_id: number;
  status: string;
  current_step: string | null;
  config: any;
  stats_json: any;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  step_jobs?: StepJob[];
}

interface PipelineLog {
  id: number;
  step: string | null;
  level: string;
  message: string;
  created_at: string;
}

interface QueueInfo {
  queued_by_store: Record<string, number>;
  running_by_store: Record<string, number>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Status helpers
// ─────────────────────────────────────────────────────────────────────────────

const STATUS_META: Record<string, { icon: any; cls: string; dot: string; label: string }> = {
  running:   { icon: Loader2,       cls: "bg-primary/10 text-primary border-primary/25",             dot: "bg-primary animate-pulse",        label: "Running" },
  queued:    { icon: Clock,         cls: "bg-secondary text-muted-foreground border-border",          dot: "bg-muted-foreground",             label: "Queued" },
  review:    { icon: Eye,           cls: "bg-amber-500/10 text-amber-400 border-amber-500/25",        dot: "bg-amber-400 animate-pulse",      label: "Review" },
  completed: { icon: CheckCircle2,  cls: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25", dot: "bg-emerald-400",                  label: "Completed" },
  failed:    { icon: XCircle,       cls: "bg-red-500/10 text-red-400 border-red-500/25",             dot: "bg-red-400",                      label: "Failed" },
  cancelled: { icon: Square,        cls: "bg-orange-500/10 text-orange-400 border-orange-500/25",    dot: "bg-orange-400",                   label: "Cancelled" },
};

function StatusBadge({ status }: { status: string }) {
  const m = STATUS_META[status] ?? STATUS_META["queued"];
  const Icon = m.icon;
  return (
    <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border", m.cls)}>
      <span className={cn("w-1.5 h-1.5 rounded-full shrink-0", m.dot)} />
      <Icon className={cn("w-3 h-3", status === "running" && "animate-spin")} />
      {m.label}
    </span>
  );
}

const STEP_LABELS: Record<string, string> = {
  process: "Processing",
  generate: "Generating",
  review: "Under Review",
  upload: "Uploading",
  sync: "Syncing",
};

const LOG_COLOR: Record<string, string> = {
  info: "text-foreground",
  warn: "text-amber-400",
  error: "text-red-400",
  ok: "text-emerald-400",
  debug: "text-muted-foreground",
};

// ─────────────────────────────────────────────────────────────────────────────
// Store badge
// ─────────────────────────────────────────────────────────────────────────────

function StoreBadge({ storeId, storeName }: { storeId: number; storeName?: string }) {
  const c = getStoreColor(storeId);
  return (
    <span className={cn("inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-medium border shrink-0", c.bg, c.text, c.border)}>
      <span className={cn("w-2 h-2 rounded-full", c.dot)} />
      {storeName ?? `Store #${storeId}`}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Inline log panel
// ─────────────────────────────────────────────────────────────────────────────

function LogPanel({ plId, isLive }: { plId: number; isLive: boolean }) {
  const [logs, setLogs] = useState<PipelineLog[]>([]);
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchLogs = useCallback(async () => {
    try {
      const r = await fetch(`/api/pipelines/${plId}/logs?limit=300`);
      if (r.ok) {
        const d = await r.json();
        setLogs(d.logs ?? []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [plId]);

  useEffect(() => {
    fetchLogs();
    if (isLive) {
      pollRef.current = setInterval(fetchLogs, 2500);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [fetchLogs, isLive]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  if (loading) return (
    <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
      <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading logs…
    </div>
  );

  if (logs.length === 0) return (
    <div className="p-4 text-sm text-muted-foreground italic">No log entries yet.</div>
  );

  return (
    <div className="max-h-64 overflow-y-auto bg-black/25 font-mono text-xs p-3 space-y-0.5">
      {logs.map((log) => (
        <div key={log.id} className="flex gap-2">
          <span className="text-muted-foreground shrink-0 w-20">
            {log.created_at ? format(new Date(log.created_at), "HH:mm:ss") : ""}
          </span>
          {log.step && (
            <span className="text-primary/60 shrink-0 w-16 truncate">[{log.step}]</span>
          )}
          <span className={cn("uppercase font-bold w-10 shrink-0", LOG_COLOR[log.level])}>
            [{log.level}]
          </span>
          <span className={cn("break-all", LOG_COLOR[log.level])}>{log.message}</span>
        </div>
      ))}
      {isLive && (
        <div className="flex items-center gap-1 text-primary/50 mt-1">
          <div className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" /> Live
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Pipeline row
// ─────────────────────────────────────────────────────────────────────────────

function PipelineRow({
  pl,
  storeName,
  logsOpen,
  onToggleLogs,
  onAction,
}: {
  pl: Pipeline;
  storeName?: string;
  logsOpen: boolean;
  onToggleLogs: () => void;
  onAction: (action: string, id: number) => void;
}) {
  const isLive = ["running", "review"].includes(pl.status);

  return (
    <>
      <tr className={cn("border-b border-border/40 hover:bg-secondary/10 transition-colors",
        logsOpen && "bg-secondary/10")}>
        {/* ID */}
        <td className="px-4 py-3 font-mono text-sm font-semibold">{pl.pl_id}</td>

        {/* Store */}
        <td className="px-4 py-3">
          <StoreBadge storeId={pl.store_id} storeName={storeName} />
        </td>

        {/* Fetch Job */}
        <td className="px-4 py-3 text-sm text-muted-foreground font-mono">
          #{pl.fetch_job_id}
        </td>

        {/* Status */}
        <td className="px-4 py-3">
          <StatusBadge status={pl.status} />
        </td>

        {/* Current step */}
        <td className="px-4 py-3 text-sm">
          {pl.current_step ? (
            <div>
              <span className="text-foreground">{STEP_LABELS[pl.current_step] ?? pl.current_step}</span>
              {pl.status === "queued" && (
                <p className="text-xs text-muted-foreground italic mt-0.5">
                  auto-starts when running finishes
                </p>
              )}
            </div>
          ) : (
            <span className="text-muted-foreground text-xs">—</span>
          )}
        </td>

        {/* Created */}
        <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
          {pl.created_at ? format(new Date(pl.created_at), "MMM d, HH:mm") : "—"}
        </td>

        {/* Actions */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-1 justify-end">
            {/* Logs toggle */}
            <button
              onClick={onToggleLogs}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-secondary hover:bg-secondary/80 text-muted-foreground hover:text-foreground transition-colors"
            >
              {logsOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              Logs
            </button>

            {/* Resume (review only) */}
            {pl.status === "review" && (
              <button
                onClick={() => onAction("resume", pl.id)}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-amber-500/20 transition-colors"
              >
                <Play className="w-3 h-3 fill-current" /> Resume
              </button>
            )}

            {/* Cancel (running | queued | review) */}
            {["running", "queued", "review"].includes(pl.status) && (
              <button
                onClick={() => onAction("cancel", pl.id)}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 transition-colors"
              >
                <Square className="w-3 h-3 fill-current" /> Cancel
              </button>
            )}

            {/* Retry (failed | cancelled) */}
            {["failed", "cancelled"].includes(pl.status) && (
              <button
                onClick={() => onAction("retry", pl.id)}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-secondary hover:bg-secondary/80 text-muted-foreground hover:text-foreground transition-colors"
              >
                <RotateCcw className="w-3 h-3" /> Retry
              </button>
            )}
          </div>
        </td>
      </tr>

      {/* Review stats row */}
      {pl.status === "review" && pl.stats_json && (
        <tr className="border-b border-amber-500/10 bg-amber-500/5">
          <td colSpan={7} className="px-4 py-2">
            <div className="flex items-center gap-4 text-sm">
              <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
              <span className="text-amber-400 font-medium">Review required before upload</span>
              <div className="flex items-center gap-3 text-xs">
                <span className="text-foreground">{pl.stats_json.total ?? 0} total</span>
                <span className="text-emerald-400">{pl.stats_json.ok ?? 0} OK</span>
                <span className="text-amber-400">{pl.stats_json.fallback ?? 0} fallback</span>
                <span className="text-red-400">{pl.stats_json.failed ?? 0} failed</span>
              </div>
              {pl.stats_json.note && (
                <span className="text-muted-foreground italic text-xs">{pl.stats_json.note}</span>
              )}
            </div>
          </td>
        </tr>
      )}

      {/* Failed error row */}
      {pl.status === "failed" && pl.error_message && (
        <tr className="border-b border-red-500/10 bg-red-500/5">
          <td colSpan={7} className="px-4 py-2">
            <div className="flex items-center gap-2 text-xs text-red-400">
              <XCircle className="w-3.5 h-3.5 shrink-0" />
              {pl.error_message}
            </div>
          </td>
        </tr>
      )}

      {/* Inline log panel */}
      {logsOpen && (
        <tr className="border-b border-border/40">
          <td colSpan={7} className="px-0">
            <LogPanel plId={pl.id} isLive={isLive} />
          </td>
        </tr>
      )}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function Pipelines() {
  const [, navigate] = useLocation();
  const { toast } = useToast();
  const { data: stores } = useStores();
  const storeMap = Object.fromEntries((stores ?? []).map((s) => [s.id, s.name]));

  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [queueInfo, setQueueInfo] = useState<QueueInfo>({ queued_by_store: {}, running_by_store: {} });
  const [loading, setLoading] = useState(true);
  const [openLogs, setOpenLogs] = useState<Set<number>>(new Set());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchPipelines = useCallback(async () => {
    try {
      const r = await fetch("/api/pipelines?limit=50");
      if (r.ok) {
        const d = await r.json();
        setPipelines(d.pipelines ?? []);
        setQueueInfo(d.queue_info ?? { queued_by_store: {}, running_by_store: {} });
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchPipelines();
    pollRef.current = setInterval(fetchPipelines, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [fetchPipelines]);

  const toggleLogs = (id: number) =>
    setOpenLogs((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });

  const handleAction = async (action: string, id: number) => {
    try {
      let url = `/api/pipelines/${id}/${action}`;
      const r = await fetch(url, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      if (action === "retry") {
        const d = await r.json();
        toast({ title: "Retry started", description: `New pipeline ${d.pl_id} created` });
      } else if (action === "resume") {
        toast({ title: "Pipeline resumed", description: "Upload step is starting…" });
      } else if (action === "cancel") {
        toast({ title: "Pipeline cancelled" });
      }
      fetchPipelines();
    } catch (e: any) {
      toast({ title: "Action failed", description: e.message, variant: "destructive" });
    }
  };

  // Queue banners: stores that have queued pipelines
  const queueBanners: { storeId: number; queued: number; runningPlId: number }[] = Object.entries(
    queueInfo.queued_by_store
  )
    .filter(([, count]) => count > 0)
    .map(([sid, count]) => ({
      storeId: parseInt(sid),
      queued: count,
      runningPlId: queueInfo.running_by_store[sid] ?? 0,
    }));

  const hasActive = pipelines.some((p) => ["running", "review"].includes(p.status));

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
            <Activity className="w-7 h-7 text-primary" />
            Pipelines
          </h1>
          <p className="text-muted-foreground mt-1">Monitor all pipeline runs and their status.</p>
        </div>
        <div className="flex items-center gap-3">
          {hasActive && (
            <span className="text-xs flex items-center gap-1.5 text-primary">
              <div className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" />
              Live
            </span>
          )}
          <button
            onClick={() => navigate("/pipeline")}
            className="px-4 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium text-sm transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2"
          >
            <Plus className="w-4 h-4" /> New Pipeline
          </button>
        </div>
      </div>

      {/* Queue banners */}
      {queueBanners.map(({ storeId, queued, runningPlId }) => {
        const c = getStoreColor(storeId);
        return (
          <div key={storeId} className={cn("px-4 py-3 rounded-xl border flex items-center gap-3 text-sm", c.bg, c.border)}>
            <Clock className={cn("w-4 h-4 shrink-0", c.text)} />
            <span>
              <strong className={c.text}>{queued} pipeline{queued > 1 ? "s" : ""}</strong>
              {" queued for "}
              <strong className={c.text}>{storeMap[storeId] ?? `Store #${storeId}`}</strong>
              {runningPlId > 0 && (
                <> — waiting for <span className="font-mono">PL-{String(runningPlId).padStart(3, "0")}</span> to finish</>
              )}
            </span>
          </div>
        );
      })}

      {/* Table */}
      <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-border/50 bg-secondary/30 text-xs text-muted-foreground uppercase tracking-wider">
                <th className="px-4 py-3 font-medium">Pipeline</th>
                <th className="px-4 py-3 font-medium">Store</th>
                <th className="px-4 py-3 font-medium">Fetch Job</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Current Step</th>
                <th className="px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-muted-foreground">
                    <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
                    Loading pipelines…
                  </td>
                </tr>
              ) : pipelines.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-16 text-center text-muted-foreground">
                    <Activity className="w-10 h-10 mx-auto mb-3 opacity-20" />
                    <p>No pipelines yet.</p>
                    <button
                      onClick={() => navigate("/pipeline")}
                      className="mt-3 px-4 py-2 rounded-xl bg-primary/10 text-primary border border-primary/20 text-sm hover:bg-primary/20 transition-colors"
                    >
                      Start your first pipeline
                    </button>
                  </td>
                </tr>
              ) : (
                pipelines.map((pl) => (
                  <PipelineRow
                    key={pl.id}
                    pl={pl}
                    storeName={storeMap[pl.store_id]}
                    logsOpen={openLogs.has(pl.id)}
                    onToggleLogs={() => toggleLogs(pl.id)}
                    onAction={handleAction}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
