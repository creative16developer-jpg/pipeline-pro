import { useState, useEffect, useRef } from "react";
import {
  Play, Square, ChevronDown, ChevronRight, RotateCcw,
  CloudDownload, Cpu, Upload, ArrowRightLeft,
  CheckCircle2, XCircle, Loader2, Clock, AlertTriangle, Zap, Info
} from "lucide-react";
import { useStores } from "@/hooks/use-stores";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

type StepKey = "fetch" | "process" | "upload" | "sync";

interface StepConfig {
  enabled: boolean;
  expanded: boolean;
}

interface FetchCfg { category_id: string; keyword: string; page_size: string; max_pages: string }
interface ProcessCfg { limit: string }
interface UploadCfg { limit: string; skip_images: boolean }
interface SyncCfg { limit: string; sync_categories: boolean; sync_attributes: boolean }

interface JobStatus {
  job_id: number;
  step: StepKey;
  status: string;
  progress_percent: number;
  total_items: number | null;
  processed_items: number | null;
  failed_items: number | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}

interface PipelineStatus {
  run_id: number;
  overall_status: string;
  jobs: JobStatus[];
}

// ─────────────────────────────────────────────────────────────────────────────
// Step meta
// ─────────────────────────────────────────────────────────────────────────────

const STEPS: { key: StepKey; label: string; desc: string; Icon: any; color: string }[] = [
  {
    key: "fetch",
    label: "Fetch",
    desc: "Pull products from Sunsky API",
    Icon: CloudDownload,
    color: "text-sky-400",
  },
  {
    key: "process",
    label: "Process",
    desc: "Download & compress images",
    Icon: Cpu,
    color: "text-amber-400",
  },
  {
    key: "upload",
    label: "Upload",
    desc: "Push drafts to WooCommerce",
    Icon: Upload,
    color: "text-emerald-400",
  },
  {
    key: "sync",
    label: "Sync",
    desc: "Sync categories & attributes",
    Icon: ArrowRightLeft,
    color: "text-violet-400",
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// Helper: status styling
// ─────────────────────────────────────────────────────────────────────────────

function statusMeta(status: string) {
  switch (status) {
    case "completed":
      return { icon: CheckCircle2, cls: "text-emerald-400", bg: "bg-emerald-500/10 border-emerald-500/20" };
    case "failed":
      return { icon: XCircle, cls: "text-red-400", bg: "bg-red-500/10 border-red-500/20" };
    case "running":
      return { icon: Loader2, cls: "text-primary animate-spin", bg: "bg-primary/10 border-primary/20" };
    case "pending":
      return { icon: Clock, cls: "text-muted-foreground", bg: "bg-secondary border-border" };
    case "cancelled":
      return { icon: Square, cls: "text-orange-400", bg: "bg-orange-500/10 border-orange-500/20" };
    default:
      return { icon: Clock, cls: "text-muted-foreground", bg: "bg-secondary border-border" };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Input helpers
// ─────────────────────────────────────────────────────────────────────────────

const inputCls =
  "w-full bg-background border border-border rounded-xl px-4 py-2.5 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all text-sm";

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 cursor-pointer rounded-full border-2 border-transparent transition-colors",
        checked ? "bg-primary" : "bg-secondary"
      )}
    >
      <span
        className={cn(
          "pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200",
          checked ? "translate-x-4" : "translate-x-0"
        )}
      />
    </button>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Step card
// ─────────────────────────────────────────────────────────────────────────────

function StepCard({
  step,
  cfg,
  onToggle,
  onExpand,
  children,
  disabled,
}: {
  step: typeof STEPS[number];
  cfg: StepConfig;
  onToggle: () => void;
  onExpand: () => void;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  const Icon = step.Icon;
  return (
    <div
      className={cn(
        "border rounded-2xl overflow-hidden transition-all",
        cfg.enabled ? "border-border/60 bg-card" : "border-border/30 bg-card/50",
        disabled && "opacity-60 pointer-events-none"
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-4 px-5 py-4">
        <label className="flex items-center gap-3 flex-1 min-w-0 cursor-pointer">
          <input
            type="checkbox"
            checked={cfg.enabled}
            onChange={onToggle}
            className="w-4 h-4 rounded accent-primary cursor-pointer"
          />
          <div
            className={cn(
              "w-9 h-9 rounded-xl flex items-center justify-center shrink-0",
              cfg.enabled ? "bg-secondary/60" : "bg-secondary/30"
            )}
          >
            <Icon className={cn("w-5 h-5", cfg.enabled ? step.color : "text-muted-foreground")} />
          </div>
          <div className="min-w-0">
            <p className={cn("font-semibold text-sm", !cfg.enabled && "text-muted-foreground")}>
              {step.label}
            </p>
            <p className="text-xs text-muted-foreground truncate">{step.desc}</p>
          </div>
        </label>

        {cfg.enabled && (
          <button
            onClick={onExpand}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded-lg hover:bg-secondary shrink-0"
          >
            Configure
            {cfg.expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>

      {/* Config section */}
      {cfg.enabled && cfg.expanded && (
        <div className="px-5 pb-5 border-t border-border/40 pt-4 space-y-3">
          {children}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Job progress row
// ─────────────────────────────────────────────────────────────────────────────

function JobRow({ job }: { job: JobStatus }) {
  const meta = statusMeta(job.status);
  const StatusIcon = meta.icon;
  const step = STEPS.find((s) => s.key === job.step);
  const Icon = step?.Icon ?? CloudDownload;

  const pct = job.progress_percent ?? 0;
  const total = job.total_items ?? 0;
  const done = job.processed_items ?? 0;
  const failed = job.failed_items ?? 0;

  return (
    <div className={cn("border rounded-xl p-4 transition-all", meta.bg)}>
      <div className="flex items-center gap-3 mb-3">
        <Icon className={cn("w-4 h-4 shrink-0", step?.color ?? "text-muted-foreground")} />
        <span className="font-semibold text-sm capitalize">{job.step}</span>
        <span className="text-xs text-muted-foreground font-mono">#{job.job_id}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <StatusIcon className={cn("w-3.5 h-3.5", meta.cls)} />
          <span className={cn("text-xs font-medium capitalize", meta.cls)}>{job.status}</span>
        </div>
      </div>

      {/* Progress bar */}
      <div className="w-full bg-black/20 rounded-full h-1.5 mb-2">
        <div
          className={cn(
            "h-1.5 rounded-full transition-all duration-500",
            job.status === "completed" ? "bg-emerald-500" :
            job.status === "failed"    ? "bg-red-500" :
            job.status === "running"   ? "bg-primary" :
                                         "bg-border"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {total > 0 ? `${done} / ${total} items${failed > 0 ? ` · ${failed} failed` : ""}` : "—"}
        </span>
        <span>{pct.toFixed(0)}%</span>
      </div>

      {job.error_message && (
        <div className="mt-2 flex items-start gap-1.5 text-xs text-red-400">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          {job.error_message}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function Pipeline() {
  const { toast } = useToast();
  const { data: stores } = useStores();

  // Global settings
  const [storeId, setStoreId] = useState("");
  const [forceRerun, setForceRerun] = useState(false);

  // Step toggles & expansion
  const [stepCfg, setStepCfg] = useState<Record<StepKey, StepConfig>>({
    fetch:   { enabled: true,  expanded: false },
    process: { enabled: true,  expanded: false },
    upload:  { enabled: true,  expanded: false },
    sync:    { enabled: true,  expanded: false },
  });

  // Per-step config
  const [fetchCfg, setFetchCfg] = useState<FetchCfg>({
    category_id: "", keyword: "", page_size: "50", max_pages: "",
  });
  const [processCfg, setProcessCfg] = useState<ProcessCfg>({ limit: "200" });
  const [uploadCfg, setUploadCfg] = useState<UploadCfg>({ limit: "200", skip_images: false });
  const [syncCfg, setSyncCfg] = useState<SyncCfg>({
    limit: "200", sync_categories: true, sync_attributes: true,
  });

  // Pipeline run state
  const [runId, setRunId] = useState<number | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [running, setRunning] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Helpers ─────────────────────────────────────────────────────────────

  const toggleStep = (key: StepKey) =>
    setStepCfg((c) => ({ ...c, [key]: { ...c[key], enabled: !c[key].enabled } }));
  const toggleExpand = (key: StepKey) =>
    setStepCfg((c) => ({ ...c, [key]: { ...c[key], expanded: !c[key].expanded } }));

  const enabledSteps = STEPS.filter((s) => stepCfg[s.key].enabled).map((s) => s.key);

  // ── Polling ─────────────────────────────────────────────────────────────

  const pollPipeline = async (id: number) => {
    try {
      const resp = await fetch(`/api/pipeline/run/${id}`);
      if (!resp.ok) return;
      const data: PipelineStatus = await resp.json();
      setPipelineStatus(data);
      if (["completed", "failed", "cancelled"].includes(data.overall_status)) {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        setRunning(false);
        if (data.overall_status === "completed") {
          toast({ title: "Pipeline complete", description: `All ${data.jobs.length} steps finished successfully.` });
        } else {
          toast({ title: "Pipeline stopped", description: `Status: ${data.overall_status}`, variant: "destructive" });
        }
      }
    } catch {
      /* ignore transient errors */
    }
  };

  useEffect(() => {
    if (!runId) return;
    pollPipeline(runId);
    pollRef.current = setInterval(() => pollPipeline(runId), 2500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [runId]);

  // ── Run ─────────────────────────────────────────────────────────────────

  const handleRun = async () => {
    if (enabledSteps.length === 0) {
      toast({ title: "No steps selected", description: "Enable at least one pipeline step.", variant: "destructive" });
      return;
    }
    const needsStore = enabledSteps.includes("upload") || enabledSteps.includes("sync");
    if (needsStore && !storeId) {
      toast({ title: "Store required", description: "Select a WooCommerce store for upload/sync steps.", variant: "destructive" });
      return;
    }

    setRunning(true);
    setPipelineStatus(null);

    const body = {
      steps: enabledSteps,
      store_id: storeId ? parseInt(storeId) : undefined,
      force_rerun: forceRerun,
      fetch_config: {
        ...(fetchCfg.category_id ? { category_id: fetchCfg.category_id } : {}),
        ...(fetchCfg.keyword ? { keyword: fetchCfg.keyword } : {}),
        page_size: parseInt(fetchCfg.page_size) || 50,
        ...(fetchCfg.max_pages ? { max_pages: parseInt(fetchCfg.max_pages) } : {}),
      },
      process_config: { limit: parseInt(processCfg.limit) || 200 },
      upload_config: {
        limit: parseInt(uploadCfg.limit) || 200,
        skip_images: uploadCfg.skip_images,
      },
      sync_config: {
        limit: parseInt(syncCfg.limit) || 200,
        sync_categories: syncCfg.sync_categories,
        sync_attributes: syncCfg.sync_attributes,
      },
    };

    try {
      const resp = await fetch("/api/pipeline/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      setRunId(data.run_id);
      toast({ title: "Pipeline started", description: `${enabledSteps.length} steps queued — running automatically.` });
    } catch (e: any) {
      toast({ title: "Failed to start pipeline", description: e.message, variant: "destructive" });
      setRunning(false);
    }
  };

  const handleAbort = async () => {
    if (!pipelineStatus) return;
    const activeJob = pipelineStatus.jobs.find((j) => j.status === "running" || j.status === "pending");
    if (!activeJob) return;
    try {
      await fetch(`/api/jobs/${activeJob.job_id}/cancel`, { method: "POST" });
      toast({ title: "Pipeline aborted", description: `Job #${activeJob.job_id} cancelled.` });
    } catch {
      /* ignore */
    }
  };

  const overall = pipelineStatus?.overall_status;
  const isDone = overall && ["completed", "failed", "cancelled"].includes(overall);

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between gap-4 items-start sm:items-center">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
            <Zap className="w-7 h-7 text-primary" />
            Pipeline Runner
          </h1>
          <p className="text-muted-foreground mt-1">
            Select steps, configure each one, then run the full pipeline sequentially in a single click.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {running && !isDone && (
            <button
              onClick={handleAbort}
              className="px-4 py-2.5 rounded-xl border border-red-500/30 text-red-400 text-sm font-medium hover:bg-red-500/10 transition-colors flex items-center gap-2"
            >
              <Square className="w-3.5 h-3.5 fill-current" /> Abort
            </button>
          )}
          <button
            onClick={isDone ? () => { setRunId(null); setPipelineStatus(null); setRunning(false); } : handleRun}
            disabled={running && !isDone}
            className="px-5 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:translate-y-0"
          >
            {running && !isDone ? (
              <><Loader2 className="w-4 h-4 animate-spin" /> Running…</>
            ) : isDone ? (
              <><RotateCcw className="w-4 h-4" /> Run Again</>
            ) : (
              <><Play className="w-4 h-4 fill-current" /> Run Pipeline</>
            )}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-6">

        {/* Left: Config */}
        <div className="space-y-4">

          {/* Global settings */}
          <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-4 flex items-center gap-2">
              <Info className="w-3.5 h-3.5" /> Global Settings
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Store */}
              <div className="sm:col-span-2">
                <label className="text-xs text-muted-foreground font-medium">Target WooCommerce Store</label>
                <select
                  value={storeId}
                  onChange={(e) => setStoreId(e.target.value)}
                  className={cn(inputCls, "mt-1.5")}
                >
                  <option value="">— Select a store (required for Upload & Sync) —</option>
                  {stores?.map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>

              {/* Force re-run */}
              <div className="flex items-start gap-3 p-3 rounded-xl bg-secondary/30 border border-border/40 sm:col-span-2">
                <Toggle checked={forceRerun} onChange={setForceRerun} />
                <div>
                  <p className="text-sm font-medium flex items-center gap-1.5">
                    <RotateCcw className="w-3.5 h-3.5 text-amber-400" />
                    Force Re-run
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Re-process and re-upload products that were already handled in a previous run.
                    Updates records in-place — no new rows are created, keeping the database lean.
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Step cards */}
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider px-1">
              Steps — {enabledSteps.length} / {STEPS.length} selected
            </h2>

            {/* Visual flow */}
            <div className="flex items-center gap-1 px-1 flex-wrap">
              {STEPS.map((step, i) => {
                const on = stepCfg[step.key].enabled;
                const Icon = step.Icon;
                return (
                  <div key={step.key} className="flex items-center gap-1">
                    <div
                      className={cn(
                        "px-3 py-1.5 rounded-lg text-xs font-medium border flex items-center gap-1.5 transition-all",
                        on
                          ? "border-border/60 bg-card text-foreground"
                          : "border-border/20 bg-card/30 text-muted-foreground opacity-40"
                      )}
                    >
                      <Icon className={cn("w-3 h-3", on ? step.color : "")} />
                      {step.label}
                    </div>
                    {i < STEPS.length - 1 && (
                      <ChevronRight className={cn("w-3.5 h-3.5", on ? "text-muted-foreground" : "text-border")} />
                    )}
                  </div>
                );
              })}
            </div>

            {STEPS.map((step) => (
              <StepCard
                key={step.key}
                step={step}
                cfg={stepCfg[step.key]}
                onToggle={() => toggleStep(step.key)}
                onExpand={() => toggleExpand(step.key)}
                disabled={running && !isDone}
              >
                {/* ── Fetch config ── */}
                {step.key === "fetch" && (
                  <>
                    <div>
                      <label className="text-xs text-muted-foreground">Category ID <span className="opacity-60">(optional)</span></label>
                      <input
                        type="text"
                        value={fetchCfg.category_id}
                        onChange={(e) => setFetchCfg((c) => ({ ...c, category_id: e.target.value }))}
                        placeholder="e.g. 50001307"
                        className={cn(inputCls, "mt-1")}
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Keyword <span className="opacity-60">(optional)</span></label>
                      <input
                        type="text"
                        value={fetchCfg.keyword}
                        onChange={(e) => setFetchCfg((c) => ({ ...c, keyword: e.target.value }))}
                        placeholder="e.g. drone"
                        className={cn(inputCls, "mt-1")}
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-muted-foreground">Page Size</label>
                        <input
                          type="number"
                          min={1}
                          max={200}
                          value={fetchCfg.page_size}
                          onChange={(e) => setFetchCfg((c) => ({ ...c, page_size: e.target.value }))}
                          className={cn(inputCls, "mt-1")}
                        />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">Max Pages <span className="opacity-60">(blank = all)</span></label>
                        <input
                          type="number"
                          min={1}
                          value={fetchCfg.max_pages}
                          onChange={(e) => setFetchCfg((c) => ({ ...c, max_pages: e.target.value }))}
                          placeholder="All"
                          className={cn(inputCls, "mt-1")}
                        />
                      </div>
                    </div>
                  </>
                )}

                {/* ── Process config ── */}
                {step.key === "process" && (
                  <div>
                    <label className="text-xs text-muted-foreground">Max Products</label>
                    <input
                      type="number"
                      min={1}
                      value={processCfg.limit}
                      onChange={(e) => setProcessCfg({ limit: e.target.value })}
                      className={cn(inputCls, "mt-1")}
                    />
                  </div>
                )}

                {/* ── Upload config ── */}
                {step.key === "upload" && (
                  <>
                    <div>
                      <label className="text-xs text-muted-foreground">Max Products</label>
                      <input
                        type="number"
                        min={1}
                        value={uploadCfg.limit}
                        onChange={(e) => setUploadCfg((c) => ({ ...c, limit: e.target.value }))}
                        className={cn(inputCls, "mt-1")}
                      />
                    </div>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={uploadCfg.skip_images}
                        onChange={(e) => setUploadCfg((c) => ({ ...c, skip_images: e.target.checked }))}
                        className="w-4 h-4 rounded accent-primary"
                      />
                      <span className="text-sm">Skip image upload</span>
                    </label>
                  </>
                )}

                {/* ── Sync config ── */}
                {step.key === "sync" && (
                  <>
                    <div>
                      <label className="text-xs text-muted-foreground">Max Products</label>
                      <input
                        type="number"
                        min={1}
                        value={syncCfg.limit}
                        onChange={(e) => setSyncCfg((c) => ({ ...c, limit: e.target.value }))}
                        className={cn(inputCls, "mt-1")}
                      />
                    </div>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={syncCfg.sync_categories}
                        onChange={(e) => setSyncCfg((c) => ({ ...c, sync_categories: e.target.checked }))}
                        className="w-4 h-4 rounded accent-primary"
                      />
                      <span className="text-sm">Sync categories</span>
                    </label>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={syncCfg.sync_attributes}
                        onChange={(e) => setSyncCfg((c) => ({ ...c, sync_attributes: e.target.checked }))}
                        className="w-4 h-4 rounded accent-primary"
                      />
                      <span className="text-sm">Sync attributes</span>
                    </label>
                  </>
                )}
              </StepCard>
            ))}
          </div>
        </div>

        {/* Right: Live progress */}
        <div className="space-y-5">
          <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-sm">
            <div className="px-5 py-4 border-b border-border/50 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                Live Progress
              </h2>
              {overall && (
                <span
                  className={cn(
                    "text-xs px-2.5 py-1 rounded-full border font-medium capitalize",
                    overall === "completed"
                      ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                      : overall === "failed"
                      ? "bg-red-500/10 text-red-400 border-red-500/20"
                      : overall === "running"
                      ? "bg-primary/10 text-primary border-primary/20"
                      : "bg-secondary text-muted-foreground border-border"
                  )}
                >
                  {overall}
                </span>
              )}
            </div>

            <div className="p-4 space-y-3">
              {!pipelineStatus ? (
                <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground">
                  <Zap className="w-10 h-10 mb-3 opacity-20" />
                  <p className="text-sm">Configure your steps and click Run Pipeline to start.</p>
                  <p className="text-xs mt-1 opacity-60">
                    Each step runs automatically after the previous one completes.
                  </p>
                </div>
              ) : (
                pipelineStatus.jobs.map((job) => (
                  <JobRow key={job.job_id} job={job} />
                ))
              )}
            </div>
          </div>

          {/* Info card */}
          <div className="bg-secondary/30 border border-border/40 rounded-2xl p-5 text-sm text-muted-foreground space-y-2">
            <p className="font-medium text-foreground text-xs uppercase tracking-wider flex items-center gap-1.5">
              <Info className="w-3.5 h-3.5 text-primary" /> How it works
            </p>
            <ul className="space-y-1.5 list-disc list-inside text-xs leading-relaxed">
              <li>Each step is created as a separate job and chained automatically</li>
              <li>If a step fails the pipeline stops — fix the issue and re-run</li>
              <li>
                <strong className="text-foreground">Force Re-run</strong> updates existing records in-place — no
                duplicate rows accumulate in the database
              </li>
              <li>You can disable individual steps to skip them (e.g. re-run only Upload + Sync)</li>
              <li>All job logs are available on the Import Jobs page</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
