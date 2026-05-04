import { useState, useEffect } from "react";
import { useLocation } from "wouter";
import {
  Play, Zap, ChevronDown, ChevronRight, RotateCcw,
  CloudDownload, Cpu, Upload, ArrowRightLeft, Sparkles,
  Info, Loader2, AlertTriangle
} from "lucide-react";
import { useStores } from "@/hooks/use-stores";
import { useToast } from "@/hooks/use-toast";
import { getStoreColor } from "@/lib/store-colors";
import { cn } from "@/lib/utils";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface FetchJob {
  id: number;
  status: string;
  total_items: number;
  config: any;
  created_at: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
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

const PIPELINE_STEPS = [
  { key: "process", label: "Process", desc: "Download & compress product images", Icon: Cpu, color: "text-amber-400" },
  { key: "generate", label: "Generate", desc: "AI content generation (optional)", Icon: Sparkles, color: "text-violet-400" },
  { key: "review", label: "Review", desc: "Pause for manual approval", Icon: Info, color: "text-sky-400" },
  { key: "upload", label: "Upload", desc: "Push products to WooCommerce", Icon: Upload, color: "text-emerald-400" },
  { key: "sync", label: "Sync", desc: "Sync categories & attributes", Icon: ArrowRightLeft, color: "text-pink-400" },
];

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function Pipeline() {
  const [, navigate] = useLocation();
  const { toast } = useToast();
  const { data: stores } = useStores();

  // Selections
  const [storeId, setStoreId] = useState("");
  const [fetchJobId, setFetchJobId] = useState("");
  const [fetchJobs, setFetchJobs] = useState<FetchJob[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);

  // Options
  const [includeGenerate, setIncludeGenerate] = useState(false);
  const [forceRerun, setForceRerun] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Per-step config
  const [processLimit, setProcessLimit] = useState("200");
  const [uploadLimit, setUploadLimit] = useState("200");
  const [uploadSkipImages, setUploadSkipImages] = useState(false);
  const [syncLimit, setSyncLimit] = useState("200");
  const [syncCategories, setSyncCategories] = useState(true);
  const [syncAttributes, setSyncAttributes] = useState(true);

  // Running state
  const [running, setRunning] = useState(false);

  // Load fetch jobs whenever store changes
  useEffect(() => {
    setFetchJobId("");
    setFetchJobs([]);
    if (!storeId) return;

    setLoadingJobs(true);
    fetch("/api/jobs?type=fetch&status=completed&limit=50")
      .then((r) => r.json())
      .then((d) => {
        const jobs: FetchJob[] = d.jobs ?? [];
        setFetchJobs(jobs);
        if (jobs.length > 0) setFetchJobId(String(jobs[0].id));
      })
      .catch(() => {})
      .finally(() => setLoadingJobs(false));
  }, [storeId]);

  const selectedStore = stores?.find((s) => String(s.id) === storeId);
  const selectedFetchJob = fetchJobs.find((j) => String(j.id) === fetchJobId);

  const handleRun = async () => {
    if (!storeId) {
      toast({ title: "Store required", description: "Select a WooCommerce store.", variant: "destructive" });
      return;
    }
    if (!fetchJobId) {
      toast({ title: "Fetch job required", description: "Select a fetch job to process.", variant: "destructive" });
      return;
    }

    setRunning(true);
    try {
      const body = {
        store_id: parseInt(storeId),
        fetch_job_id: parseInt(fetchJobId),
        include_generate: includeGenerate,
        force_rerun: forceRerun,
        process_config: { limit: parseInt(processLimit) || 200 },
        upload_config: { limit: parseInt(uploadLimit) || 200, skip_images: uploadSkipImages },
        sync_config: {
          limit: parseInt(syncLimit) || 200,
          sync_categories: syncCategories,
          sync_attributes: syncAttributes,
        },
      };

      const r = await fetch("/api/pipelines", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();

      toast({
        title: d.status === "queued" ? "Pipeline queued" : "Pipeline started",
        description: `${d.pl_id} — ${d.status === "queued" ? "will auto-start when the current run finishes" : "processing step is running"}`,
      });
      navigate("/pipelines");
    } catch (e: any) {
      toast({ title: "Failed to start pipeline", description: e.message, variant: "destructive" });
      setRunning(false);
    }
  };

  const storeColor = storeId ? getStoreColor(parseInt(storeId)) : null;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
          <Zap className="w-7 h-7 text-primary" />
          New Pipeline
        </h1>
        <p className="text-muted-foreground mt-1">
          Select a store and fetch job, then start the automated pipeline.
        </p>
      </div>

      {/* Pipeline flow diagram */}
      <div className="bg-secondary/20 border border-border/40 rounded-2xl p-4">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">Pipeline Flow</p>
        <div className="flex flex-wrap items-center gap-1.5">
          {PIPELINE_STEPS.map((step, i) => {
            const Icon = step.Icon;
            const isOptional = step.key === "generate";
            const isAlways = step.key === "review";
            return (
              <div key={step.key} className="flex items-center gap-1.5">
                <div className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium",
                  isAlways ? "border-sky-500/25 bg-sky-500/10 text-sky-400" :
                  isOptional && !includeGenerate ? "border-border/25 bg-secondary/30 text-muted-foreground opacity-50" :
                  "border-border/40 bg-secondary/40 text-foreground"
                )}>
                  <Icon className={cn("w-3 h-3", step.color)} />
                  {step.label}
                  {isOptional && <span className="text-[10px] opacity-70">(opt)</span>}
                  {isAlways && <span className="text-[10px] bg-sky-500/20 text-sky-300 px-1 rounded">pause</span>}
                </div>
                {i < PIPELINE_STEPS.length - 1 && (
                  <ChevronRight className="w-3 h-3 text-border shrink-0" />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Main config card */}
      <div className="bg-card border border-border/50 rounded-2xl p-6 shadow-sm space-y-5">

        {/* Store */}
        <div>
          <label className="text-sm font-medium">Target Store <span className="text-red-400">*</span></label>
          <div className="mt-1.5 flex gap-2 items-center">
            <select
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              className={inputCls}
            >
              <option value="">— Select a store —</option>
              {stores?.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
            {storeColor && selectedStore && (
              <span className={cn("px-2.5 py-1.5 rounded-lg border text-xs font-medium whitespace-nowrap shrink-0",
                storeColor.bg, storeColor.text, storeColor.border)}>
                <span className={cn("inline-block w-2 h-2 rounded-full mr-1.5", storeColor.dot)} />
                {selectedStore.name}
              </span>
            )}
          </div>
        </div>

        {/* Fetch Job */}
        <div>
          <label className="text-sm font-medium">
            Fetch Job <span className="text-red-400">*</span>
            <span className="text-muted-foreground font-normal text-xs ml-2">
              (completed fetch jobs — each represents one batch of products)
            </span>
          </label>
          {!storeId ? (
            <p className="mt-1.5 text-sm text-muted-foreground italic">Select a store first</p>
          ) : loadingJobs ? (
            <div className="mt-1.5 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading fetch jobs…
            </div>
          ) : fetchJobs.length === 0 ? (
            <div className="mt-1.5 flex items-center gap-2 text-sm text-amber-400">
              <AlertTriangle className="w-4 h-4" />
              No completed fetch jobs found. Run a fetch job from Sunsky Fetch first.
            </div>
          ) : (
            <select
              value={fetchJobId}
              onChange={(e) => setFetchJobId(e.target.value)}
              className={cn(inputCls, "mt-1.5")}
            >
              <option value="">— Select a fetch job —</option>
              {fetchJobs.map((j) => (
                <option key={j.id} value={j.id}>
                  #{j.id} · {j.total_items} products
                  {j.config?.category_id ? ` · cat: ${j.config.category_id}` : ""}
                  {j.config?.keyword ? ` · "${j.config.keyword}"` : ""}
                  {j.created_at ? ` · ${new Date(j.created_at).toLocaleDateString()}` : ""}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Options */}
        <div className="space-y-3 pt-1">
          <div className="flex items-start gap-3 p-3 rounded-xl bg-secondary/30 border border-border/40">
            <Toggle checked={includeGenerate} onChange={setIncludeGenerate} />
            <div>
              <p className="text-sm font-medium flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-violet-400" />
                Include Content Generation
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Run AI content generation before upload. Uses settings from the Content Generation page.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 p-3 rounded-xl bg-secondary/30 border border-border/40">
            <Toggle checked={forceRerun} onChange={setForceRerun} />
            <div>
              <p className="text-sm font-medium flex items-center gap-1.5">
                <RotateCcw className="w-3.5 h-3.5 text-amber-400" />
                Force Re-run
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Re-process and re-upload already handled products. Updates in-place — no duplicate rows.
              </p>
            </div>
          </div>
        </div>

        {/* Advanced config (collapsible) */}
        <div>
          <button
            onClick={() => setShowAdvanced((x) => !x)}
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            {showAdvanced ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            Advanced step configuration
          </button>

          {showAdvanced && (
            <div className="mt-4 space-y-4 border-t border-border/40 pt-4">
              {/* Process */}
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5 mb-2">
                  <Cpu className="w-3 h-3 text-amber-400" /> Process
                </p>
                <label className="text-xs text-muted-foreground">Max products</label>
                <input
                  type="number"
                  value={processLimit}
                  onChange={(e) => setProcessLimit(e.target.value)}
                  min={1}
                  className={cn(inputCls, "mt-1")}
                />
              </div>

              {/* Upload */}
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5 mb-2">
                  <Upload className="w-3 h-3 text-emerald-400" /> Upload
                </p>
                <label className="text-xs text-muted-foreground">Max products</label>
                <input
                  type="number"
                  value={uploadLimit}
                  onChange={(e) => setUploadLimit(e.target.value)}
                  min={1}
                  className={cn(inputCls, "mt-1 mb-2")}
                />
                <label className="flex items-center gap-2.5 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={uploadSkipImages}
                    onChange={(e) => setUploadSkipImages(e.target.checked)}
                    className="w-4 h-4 rounded accent-primary"
                  />
                  Skip image upload
                </label>
              </div>

              {/* Sync */}
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5 mb-2">
                  <ArrowRightLeft className="w-3 h-3 text-pink-400" /> Sync
                </p>
                <label className="text-xs text-muted-foreground">Max products</label>
                <input
                  type="number"
                  value={syncLimit}
                  onChange={(e) => setSyncLimit(e.target.value)}
                  min={1}
                  className={cn(inputCls, "mt-1 mb-2")}
                />
                <div className="space-y-2">
                  <label className="flex items-center gap-2.5 cursor-pointer text-sm">
                    <input type="checkbox" checked={syncCategories}
                      onChange={(e) => setSyncCategories(e.target.checked)}
                      className="w-4 h-4 rounded accent-primary" />
                    Sync categories
                  </label>
                  <label className="flex items-center gap-2.5 cursor-pointer text-sm">
                    <input type="checkbox" checked={syncAttributes}
                      onChange={(e) => setSyncAttributes(e.target.checked)}
                      className="w-4 h-4 rounded accent-primary" />
                    Sync attributes
                  </label>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Review note */}
        <div className="flex items-start gap-2.5 p-3 rounded-xl bg-sky-500/10 border border-sky-500/20 text-sm text-sky-400">
          <Info className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <p className="font-medium">Automatic review pause</p>
            <p className="text-xs mt-0.5 text-sky-400/80">
              The pipeline pauses after processing for you to review before anything is uploaded to WooCommerce.
              Click Resume on the Pipelines page when ready.
            </p>
          </div>
        </div>

        {/* Run button */}
        <button
          onClick={handleRun}
          disabled={running || !storeId || !fetchJobId}
          className="w-full py-3 rounded-xl bg-primary text-primary-foreground font-medium shadow-lg shadow-primary/20 hover:shadow-primary/40 hover:-translate-y-0.5 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed disabled:translate-y-0 disabled:shadow-none"
        >
          {running ? (
            <><Loader2 className="w-4 h-4 animate-spin" /> Starting…</>
          ) : (
            <><Play className="w-4 h-4 fill-current" /> Run Pipeline</>
          )}
        </button>
      </div>

      {/* Info */}
      <div className="bg-secondary/20 border border-border/30 rounded-2xl p-5 text-sm text-muted-foreground space-y-2">
        <p className="font-medium text-foreground text-xs uppercase tracking-wider flex items-center gap-1.5">
          <Info className="w-3.5 h-3.5 text-primary" /> What happens next
        </p>
        <ul className="space-y-1.5 list-disc list-inside text-xs leading-relaxed">
          <li><strong className="text-foreground">Process</strong> — images downloaded, compressed, watermarked</li>
          {includeGenerate && <li><strong className="text-foreground">Generate</strong> — AI content created for each product</li>}
          <li><strong className="text-foreground">Review</strong> — pipeline pauses; you approve before upload</li>
          <li><strong className="text-foreground">Upload</strong> — products pushed as drafts to WooCommerce</li>
          <li><strong className="text-foreground">Sync</strong> — categories and attributes assigned</li>
          <li>If another pipeline is running for this store, yours will be queued and auto-start automatically</li>
        </ul>
      </div>
    </div>
  );
}
