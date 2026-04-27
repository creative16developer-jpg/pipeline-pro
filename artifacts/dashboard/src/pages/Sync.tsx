import { useState, useEffect, useRef } from "react";
import { RefreshCw, ChevronRight, CloudDownload, Cpu, Upload, ArrowRightLeft, Play, CheckCircle2, Circle, Info } from "lucide-react";
import { useStores } from "@/hooks/use-stores";
import { useJobs, useCreateJob } from "@/hooks/use-jobs";
import { useToast } from "@/hooks/use-toast";
import { StatusBadge } from "@/components/StatusBadge";

const STEP_LABELS = [
  { icon: CloudDownload, label: "Fetch", desc: "Pull products from Sunsky" },
  { icon: Cpu,          label: "Process", desc: "Compress & watermark images" },
  { icon: Upload,       label: "Upload", desc: "Push drafts to WooCommerce" },
  { icon: ArrowRightLeft, label: "Sync", desc: "Sync categories & attributes", active: true },
];

const inputClass =
  "w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all text-sm";

export default function Sync() {
  const { toast } = useToast();
  const { data: stores } = useStores();
  const createJob = useCreateJob();

  const [storeId, setStoreId] = useState("");
  const [syncCategories, setSyncCategories] = useState(true);
  const [syncAttributes, setSyncAttributes] = useState(true);
  const [sourceJobId, setSourceJobId] = useState("");
  const [limit, setLimit] = useState("200");
  const [runningJobId, setRunningJobId] = useState<number | null>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const logBottomRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data: allJobsData } = useJobs({ page: 1, limit: 100 });
  const allJobs = allJobsData?.jobs ?? [];
  const uploadJobs = allJobs.filter(j => j.type === "upload" && j.status === "completed");

  const pollJob = async (jobId: number) => {
    try {
      const [jobRes, logRes] = await Promise.all([
        fetch(`/api/jobs/${jobId}`),
        fetch(`/api/jobs/${jobId}/logs?limit=200`),
      ]);
      if (jobRes.ok) {
        const job = await jobRes.json();
        setJobStatus(job.status);
        if (["completed", "failed", "cancelled"].includes(job.status)) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }
      if (logRes.ok) {
        const data = await logRes.json();
        setLogs(Array.isArray(data) ? data : data.logs ?? []);
      }
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    if (!runningJobId) return;
    pollJob(runningJobId);
    pollRef.current = setInterval(() => pollJob(runningJobId), 2500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [runningJobId]);

  useEffect(() => {
    logBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!storeId) {
      toast({ title: "Store required", description: "Please select a WooCommerce store.", variant: "destructive" });
      return;
    }
    if (!syncCategories && !syncAttributes) {
      toast({ title: "Nothing to sync", description: "Enable at least one option.", variant: "destructive" });
      return;
    }

    setLogs([]);
    setJobStatus(null);

    createJob.mutate({
      data: {
        type: "sync",
        storeId: parseInt(storeId),
        sourceJobId: sourceJobId ? parseInt(sourceJobId) : undefined,
        config: {
          store_id: parseInt(storeId),
          sync_categories: syncCategories,
          sync_attributes: syncAttributes,
          limit: parseInt(limit) || 200,
          ...(sourceJobId ? { source_job_id: parseInt(sourceJobId) } : {}),
        },
      },
    }, {
      onSuccess: (job: any) => {
        const id = job?.id ?? job?.jobId;
        if (id) {
          setRunningJobId(id);
          toast({ title: "Sync job started", description: `Job #${id} is running.` });
        }
      },
      onError: (err: any) => {
        toast({ title: "Failed to start sync", description: err.message, variant: "destructive" });
      },
    });
  };

  const levelColor: Record<string, string> = {
    debug: "text-muted-foreground",
    info: "text-foreground",
    warn: "text-amber-400",
    error: "text-red-400",
  };

  const isDone = jobStatus && ["completed", "failed", "cancelled"].includes(jobStatus);
  const isRunning = runningJobId && !isDone;

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
          <ArrowRightLeft className="w-8 h-8 text-primary" /> Sync Categories & Attributes
        </h1>
        <p className="text-muted-foreground mt-2">
          Push Sunsky categories into WooCommerce and attach product attributes to your uploaded drafts.
        </p>
      </div>

      {/* 4-step pipeline flow */}
      <div className="bg-secondary/20 border border-border/40 rounded-2xl p-5">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-4">Pipeline Steps</p>
        <div className="flex flex-wrap items-center gap-2">
          {STEP_LABELS.map((step, i) => {
            const Icon = step.icon;
            return (
              <div key={i} className="flex items-center gap-2">
                <div className={`flex items-center gap-2 px-3 py-2 rounded-xl border transition-all ${
                  step.active
                    ? "bg-primary/10 border-primary/30 text-primary"
                    : "border-border/30 text-muted-foreground"
                }`}>
                  <Icon className="w-4 h-4" />
                  <div>
                    <div className={`text-xs font-semibold ${step.active ? "text-primary" : "text-foreground"}`}>
                      Step {i + 1}: {step.label}
                    </div>
                    <div className="text-xs text-muted-foreground hidden sm:block">{step.desc}</div>
                  </div>
                  {step.active && <span className="ml-1 text-[10px] bg-primary text-primary-foreground rounded px-1.5 py-0.5 font-bold">YOU ARE HERE</span>}
                </div>
                {i < STEP_LABELS.length - 1 && <ChevronRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />}
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
        {/* Config panel */}
        <div className="md:col-span-2">
          <form onSubmit={handleStart} className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5 space-y-6">
            <h2 className="text-xl font-display font-semibold border-b border-border/50 pb-2">Sync Configuration</h2>

            {/* Store */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">Target WooCommerce Store</label>
              <select value={storeId} onChange={(e) => setStoreId(e.target.value)} className={inputClass} required>
                <option value="">— Select a store —</option>
                {stores?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>

            {/* What to sync */}
            <div className="space-y-3">
              <label className="text-sm font-medium text-foreground">What to Sync</label>
              <div className="space-y-2 p-4 bg-secondary/20 rounded-xl border border-border/50">
                <label className="flex items-start gap-3 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={syncCategories}
                    onChange={(e) => setSyncCategories(e.target.checked)}
                    className="mt-0.5 w-4 h-4 rounded accent-primary"
                  />
                  <div>
                    <div className="text-sm font-medium group-hover:text-primary transition-colors">Sync Categories</div>
                    <div className="text-xs text-muted-foreground">
                      Create Sunsky parent + sub-categories in WooCommerce and assign them to your uploaded products.
                    </div>
                  </div>
                </label>
                <label className="flex items-start gap-3 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={syncAttributes}
                    onChange={(e) => setSyncAttributes(e.target.checked)}
                    className="mt-0.5 w-4 h-4 rounded accent-primary"
                  />
                  <div>
                    <div className="text-sm font-medium group-hover:text-primary transition-colors">Sync Product Attributes</div>
                    <div className="text-xs text-muted-foreground">
                      Extract spec table key-values and variant options from Sunsky data and create WooCommerce global attributes + terms.
                    </div>
                  </div>
                </label>
              </div>
            </div>

            {/* Source job filter */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">
                Scope to Upload Job <span className="text-muted-foreground font-normal">(optional)</span>
              </label>
              <select value={sourceJobId} onChange={(e) => setSourceJobId(e.target.value)} className={inputClass}>
                <option value="">— All uploaded products —</option>
                {uploadJobs.map(j => (
                  <option key={j.id} value={j.id}>
                    #{j.id} · UPLOAD · {j.totalItems} products
                    {(j.config as any)?.category_id ? ` · cat: ${(j.config as any).category_id}` : ""}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                Leave blank to sync attributes for ALL uploaded products. Selecting a job scopes it to products from that batch.
              </p>
            </div>

            {/* Limit */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">Max Products to Sync Attributes For</label>
              <input
                type="number"
                min="1"
                max="1000"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                className={inputClass}
              />
            </div>

            <div className="pt-2">
              <button
                type="submit"
                disabled={createJob.isPending || !!isRunning}
                className="w-full py-4 rounded-xl bg-primary text-primary-foreground font-medium text-lg shadow-lg shadow-primary/25 hover:shadow-xl hover:shadow-primary/40 hover:-translate-y-0.5 transition-all disabled:opacity-50 disabled:transform-none flex items-center justify-center gap-3"
              >
                {createJob.isPending || isRunning
                  ? <><div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" /> Syncing…</>
                  : <><Play className="w-5 h-5 fill-current" /> Start Sync</>}
              </button>
            </div>
          </form>
        </div>

        {/* Info sidebar */}
        <div className="space-y-6">
          <div className="bg-secondary/30 border border-border rounded-2xl p-5">
            <h3 className="font-medium flex items-center gap-2 mb-3"><Info className="w-4 h-4 text-primary" /> What this does</h3>
            <ul className="text-sm text-muted-foreground leading-relaxed space-y-2 list-disc list-inside">
              <li>Reads every Sunsky parent + child category</li>
              <li>Creates missing categories in WooCommerce</li>
              <li>Assigns the correct category to each uploaded product</li>
              <li>Parses Sunsky spec tables (colour, size, material…)</li>
              <li>Creates WooCommerce global attributes and terms</li>
              <li>Attaches attributes to your WooCommerce draft products</li>
            </ul>
          </div>

          {/* Running job status */}
          {runningJobId && (
            <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-lg shadow-black/5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-display font-medium text-lg">Job #{runningJobId}</h3>
                {jobStatus && <StatusBadge status={jobStatus} />}
              </div>

              {isDone ? (
                <div className={`flex items-center gap-2 text-sm font-medium ${
                  jobStatus === "completed" ? "text-emerald-400" : "text-red-400"
                }`}>
                  <CheckCircle2 className="w-4 h-4" />
                  {jobStatus === "completed" ? "Sync completed successfully" : `Sync ${jobStatus}`}
                </div>
              ) : (
                <div className="flex items-center gap-2 text-sm text-primary">
                  <div className="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                  Running…
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Live log output */}
      {logs.length > 0 && (
        <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
          <div className="flex items-center justify-between px-5 py-3 border-b border-border/50 bg-secondary/30">
            <h3 className="font-display font-semibold">Sync Log</h3>
            {isRunning && <div className="flex items-center gap-2 text-xs text-primary">
              <div className="w-2 h-2 bg-primary rounded-full animate-pulse" /> Live
            </div>}
          </div>
          <div className="p-4 max-h-80 overflow-y-auto font-mono text-xs space-y-0.5 bg-black/20">
            {logs.map((log, i) => (
              <div key={i} className={`${levelColor[log.level ?? "info"] ?? "text-foreground"}`}>
                <span className="text-muted-foreground mr-2">
                  {log.created_at ? new Date(log.created_at).toLocaleTimeString() : ""}
                </span>
                <span className={`mr-2 uppercase font-bold text-[10px] ${levelColor[log.level ?? "info"]}`}>
                  [{log.level ?? "info"}]
                </span>
                {log.message}
              </div>
            ))}
            <div ref={logBottomRef} />
          </div>
        </div>
      )}
    </div>
  );
}
