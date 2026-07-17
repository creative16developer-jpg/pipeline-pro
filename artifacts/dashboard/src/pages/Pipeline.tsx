import { useState, useEffect } from "react";
import { useLocation, useSearch } from "wouter";
import {
  Play, Zap, ChevronDown, ChevronRight, RotateCcw,
  CloudDownload, Cpu, Upload, ArrowRightLeft, Sparkles,
  Info, Loader2, AlertTriangle, FileText, Layers, Check
} from "lucide-react";
import { useStores } from "@/hooks/use-stores";
import { useToast } from "@/hooks/use-toast";
import { getStoreColor } from "@/lib/store-colors";
import { cn } from "@/lib/utils";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

type ProductSource = "sunsky" | "existing" | "csv";

interface SourceJob {
  id: number;
  type: "fetch" | "csv_import";
  status: string;
  totalItems: number;
  config: any;
  createdAt: string;
}

interface SunskyCategory {
  id: string;
  name: string;
  parentId?: string | null;
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
        "relative inline-flex h-5 w-9 cursor-pointer rounded-full border-2 border-transparent transition-colors shrink-0",
        checked ? "bg-primary" : "bg-secondary"
      )}
    >
      <span className={cn(
        "pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200",
        checked ? "translate-x-4" : "translate-x-0"
      )} />
    </button>
  );
}

const PIPELINE_STEPS = [
  { key: "process",  label: "Process",  Icon: Cpu,           color: "text-amber-400",   optional: false },
  { key: "enrich",   label: "Enrich",   Icon: Layers,        color: "text-orange-400",  optional: true  },
  { key: "generate", label: "Generate", Icon: Sparkles,      color: "text-violet-400",  optional: true  },
  { key: "review",   label: "Review",   Icon: Info,          color: "text-sky-400",     optional: false },
  { key: "upload",   label: "Upload",   Icon: Upload,        color: "text-emerald-400", optional: false },
  { key: "sync",     label: "Sync",     Icon: ArrowRightLeft,color: "text-pink-400",    optional: false },
];

function jobLabel(j: SourceJob): string {
  const date = j.createdAt ? new Date(j.createdAt).toLocaleDateString() : "";
  if (j.type === "csv_import") {
    const filename = j.config?.filename || "CSV";
    return `#${j.id} · CSV: ${filename} · ${j.totalItems} products${date ? ` · ${date}` : ""}`;
  }
  const cat = j.config?.category_id ? ` · cat: ${j.config.category_id}` : "";
  const kw  = j.config?.keyword     ? ` · "${j.config.keyword}"`         : "";
  return `#${j.id} · ${j.totalItems} products${cat}${kw}${date ? ` · ${date}` : ""}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Source radio button
// ─────────────────────────────────────────────────────────────────────────────

function SourceRadio({
  value, current, onChange, label,
}: { value: ProductSource; current: ProductSource; onChange: (v: ProductSource) => void; label: string }) {
  const active = value === current;
  return (
    <button
      type="button"
      onClick={() => onChange(value)}
      className={cn(
        "flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium transition-all",
        active
          ? "border-primary bg-primary/10 text-primary"
          : "border-border text-muted-foreground hover:border-border/80 hover:text-foreground"
      )}
    >
      <span className={cn(
        "w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0",
        active ? "border-primary" : "border-border"
      )}>
        {active && <span className="w-2 h-2 rounded-full bg-primary" />}
      </span>
      {label}
    </button>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function Pipeline() {
  const [, navigate] = useLocation();
  const search = useSearch();
  const { toast } = useToast();
  const { data: stores } = useStores();

  const urlSource = new URLSearchParams(search).get("source");
  const defaultSource: ProductSource = urlSource === "csv" ? "csv" : "sunsky";

  // ── Source selection ───────────────────────────────────────────────────────
  const [productSource, setProductSource] = useState<ProductSource>(defaultSource);

  // ── Store ──────────────────────────────────────────────────────────────────
  const [storeId, setStoreId] = useState("");

  // ── Sunsky fetch mode ──────────────────────────────────────────────────────
  const [rootCats, setRootCats]   = useState<SunskyCategory[]>([]);
  const [subCats, setSubCats]     = useState<SunskyCategory[]>([]);
  const [parentCatId, setParentCatId] = useState("");
  const [subCatId, setSubCatId]   = useState("");
  const [fetchLimit, setFetchLimit] = useState("50");
  const [fetchPage, setFetchPage]   = useState("1");
  const [loadingRootCats, setLoadingRootCats] = useState(false);
  const [loadingSubCats, setLoadingSubCats]   = useState(false);
  const [fetching, setFetching]   = useState(false);

  // ── Existing / CSV mode ────────────────────────────────────────────────────
  const [fetchJobId, setFetchJobId]   = useState("");
  const [sourceJobs, setSourceJobs]   = useState<SourceJob[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);

  // ── Options ────────────────────────────────────────────────────────────────
  const [includeEnrich,   setIncludeEnrich]   = useState(false);
  const [includeGenerate, setIncludeGenerate] = useState(false);
  const [forceRerun,      setForceRerun]      = useState(false);
  const [showAdvanced,    setShowAdvanced]    = useState(false);

  // ── Advanced step config ───────────────────────────────────────────────────
  const [processLimit, setProcessLimit]       = useState("200");
  const [uploadLimit, setUploadLimit]         = useState("200");
  const [uploadSkipImages, setUploadSkipImages] = useState(false);
  const [syncLimit, setSyncLimit]             = useState("200");
  const [syncCategories, setSyncCategories]   = useState(true);
  const [syncAttributes, setSyncAttributes]   = useState(true);

  const [running, setRunning] = useState(false);

  // ── Load root Sunsky categories once ──────────────────────────────────────
  useEffect(() => {
    setLoadingRootCats(true);
    fetch("/api/sunsky/categories?parent_id=0")
      .then((r) => r.json())
      .then((d: SunskyCategory[]) => setRootCats(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoadingRootCats(false));
  }, []);

  // ── Load sub-categories when parent changes ────────────────────────────────
  useEffect(() => {
    setSubCatId("");
    setSubCats([]);
    if (!parentCatId) return;
    setLoadingSubCats(true);
    fetch(`/api/sunsky/categories?parent_id=${encodeURIComponent(parentCatId)}`)
      .then((r) => r.json())
      .then((d: SunskyCategory[]) => setSubCats(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoadingSubCats(false));
  }, [parentCatId]);

  // ── Load source jobs when store changes ───────────────────────────────────
  useEffect(() => {
    setFetchJobId("");
    setSourceJobs([]);
    if (!storeId) return;
    setLoadingJobs(true);
    Promise.all([
      fetch("/api/jobs?type=fetch&status=completed&limit=50").then((r) => r.json()),
      fetch("/api/jobs?type=csv_import&status=completed&limit=50").then((r) => r.json()),
    ])
      .then(([fetchData, csvData]) => {
        const fetchJobs: SourceJob[] = (fetchData.jobs ?? []).map((j: any) => ({ ...j, type: "fetch" as const }));
        const csvJobs:   SourceJob[] = (csvData.jobs   ?? []).map((j: any) => ({ ...j, type: "csv_import" as const }));
        const all = [...fetchJobs, ...csvJobs].sort(
          (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
        );
        setSourceJobs(all);
        if (productSource !== "sunsky" && all.length > 0) setFetchJobId(String(all[0].id));
      })
      .catch(() => {})
      .finally(() => setLoadingJobs(false));
  }, [storeId]);

  const selectedStore = stores?.find((s) => String(s.id) === storeId);
  const storeColor    = storeId ? getStoreColor(parseInt(storeId)) : null;
  const fetchJobs     = sourceJobs.filter((j) => j.type === "fetch");
  const csvJobs       = sourceJobs.filter((j) => j.type === "csv_import");
  const selectedJob   = sourceJobs.find((j) => String(j.id) === fetchJobId);

  // ── Run pipeline ───────────────────────────────────────────────────────────
  const handleRun = async () => {
    if (!storeId) {
      toast({ title: "Store required", description: "Select a WooCommerce store.", variant: "destructive" });
      return;
    }

    let pipelineFetchJobId: number;

    if (productSource === "sunsky") {
      // Step 1 — run Sunsky fetch inline
      setFetching(true);
      try {
        const categoryId = subCatId || parentCatId || undefined;
        const fetchRes = await fetch("/api/sunsky/fetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            store_id:    parseInt(storeId),
            category_id: categoryId,
            limit:       parseInt(fetchLimit) || 50,
            page:        parseInt(fetchPage)  || 1,
          }),
        });
        if (!fetchRes.ok) throw new Error(await fetchRes.text());
        const fetchData = await fetchRes.json();

        if (fetchData.fetched === 0) {
          toast({
            title: "No products found",
            description: "The selected category returned 0 products. Try a different category.",
            variant: "destructive",
          });
          setFetching(false);
          return;
        }

        toast({
          title: `Fetched ${fetchData.fetched} products`,
          description: `${fetchData.saved} new · ${fetchData.skipped} already in DB. Starting pipeline…`,
        });
        pipelineFetchJobId = fetchData.job_id;
      } catch (e: any) {
        toast({ title: "Fetch failed", description: e.message, variant: "destructive" });
        setFetching(false);
        return;
      }
      setFetching(false);
    } else {
      if (!fetchJobId) {
        toast({ title: "Source required", description: "Select a fetch job or CSV import.", variant: "destructive" });
        return;
      }
      pipelineFetchJobId = parseInt(fetchJobId);
    }

    // Step 2 — start pipeline
    setRunning(true);
    try {
      let contentGenConfig = {};
      if (includeGenerate) {
        try {
          const r = await fetch("/api/generate/saved-config");
          if (r.ok) contentGenConfig = await r.json();
        } catch (_) {}
      }

      const body = {
        store_id:         parseInt(storeId),
        fetch_job_id:     pipelineFetchJobId,
        include_enrich:   includeEnrich,
        include_generate: includeGenerate,
        force_rerun:      forceRerun,
        process_config:   { limit: parseInt(processLimit) || 200 },
        upload_config:    { limit: parseInt(uploadLimit) || 200, skip_images: uploadSkipImages },
        sync_config: {
          limit:           parseInt(syncLimit) || 200,
          sync_categories: syncCategories,
          sync_attributes: syncAttributes,
        },
        content_gen_config: contentGenConfig,
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
        description: `${d.pl_id} — ${d.status === "queued" ? "will auto-start when current run finishes" : "processing step is running"}`,
      });
      navigate("/pipelines");
    } catch (e: any) {
      toast({ title: "Failed to start pipeline", description: e.message, variant: "destructive" });
      setRunning(false);
    }
  };

  const isRunDisabled = running || fetching || !storeId || (productSource !== "sunsky" && !fetchJobId);

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
          <Zap className="w-7 h-7 text-primary" />
          New Pipeline
        </h1>
        <p className="text-muted-foreground mt-1">
          Select a store and product source, then start the automated pipeline.
        </p>
      </div>

      {/* Pipeline flow diagram */}
      <div className="bg-secondary/20 border border-border/40 rounded-2xl p-4">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">Pipeline Flow</p>
        <div className="flex flex-wrap items-center gap-1.5">
          {PIPELINE_STEPS.map((step, i) => {
            const Icon = step.Icon;
            const isPause = step.key === "review";
            const isActive =
              step.key === "enrich"   ? includeEnrich :
              step.key === "generate" ? includeGenerate :
              true;
            return (
              <div key={step.key} className="flex items-center gap-1.5">
                <div className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium",
                  isPause ? "border-sky-500/25 bg-sky-500/10 text-sky-400" :
                  !isActive ? "border-border/25 bg-secondary/30 text-muted-foreground opacity-40" :
                  "border-border/40 bg-secondary/40 text-foreground"
                )}>
                  <Icon className={cn("w-3 h-3", isActive ? step.color : "text-muted-foreground")} />
                  {step.label}
                  {step.optional && <span className="text-[10px] opacity-70">(opt)</span>}
                  {isPause && <span className="text-[10px] bg-sky-500/20 text-sky-300 px-1 rounded">pause</span>}
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

        {/* Target Store */}
        <div>
          <label className="text-sm font-medium">
            Target Store <span className="text-red-400">*</span>
          </label>
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

        {/* Product Source radio */}
        <div>
          <label className="text-sm font-medium">
            Product Source <span className="text-red-400">*</span>
          </label>
          <div className="mt-2 flex flex-wrap gap-2">
            <SourceRadio value="sunsky"   current={productSource} onChange={setProductSource} label="Fetch from Sunsky" />
            <SourceRadio value="existing" current={productSource} onChange={setProductSource} label="Use existing fetch job" />
            <SourceRadio value="csv"      current={productSource} onChange={setProductSource} label="CSV import" />
          </div>

          {/* ── Sunsky fetch fields ── */}
          {productSource === "sunsky" && (
            <div className="mt-3 space-y-3 p-4 rounded-xl bg-secondary/30 border border-border/40">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Parent Category</label>
                  <select
                    value={parentCatId}
                    onChange={(e) => setParentCatId(e.target.value)}
                    className={inputCls}
                    disabled={loadingRootCats}
                  >
                    <option value="">— All categories —</option>
                    {loadingRootCats
                      ? <option disabled>Loading…</option>
                      : rootCats.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))
                    }
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Sub-Category</label>
                  <select
                    value={subCatId}
                    onChange={(e) => setSubCatId(e.target.value)}
                    className={inputCls}
                    disabled={!parentCatId || loadingSubCats}
                  >
                    <option value="">— All sub-categories —</option>
                    {loadingSubCats
                      ? <option disabled>Loading…</option>
                      : subCats.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))
                    }
                  </select>
                </div>
              </div>
              <div className="flex items-end gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Page</label>
                  <input
                    type="number"
                    value={fetchPage}
                    onChange={(e) => setFetchPage(e.target.value)}
                    min={1}
                    className={cn(inputCls, "max-w-[90px]")}
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Limit</label>
                  <input
                    type="number"
                    value={fetchLimit}
                    onChange={(e) => setFetchLimit(e.target.value)}
                    min={1}
                    max={500}
                    className={cn(inputCls, "max-w-[120px]")}
                  />
                </div>
              </div>
              <div className="flex items-start gap-2 text-xs text-sky-400 bg-sky-500/10 border border-sky-500/20 rounded-lg px-3 py-2">
                <CloudDownload className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span>
                  Products will be fetched from Sunsky first, then the pipeline will start automatically.
                </span>
              </div>
            </div>
          )}

          {/* ── Existing fetch job fields ── */}
          {productSource === "existing" && (
            <div className="mt-3">
              {!storeId ? (
                <p className="text-sm text-muted-foreground italic">Select a store first</p>
              ) : loadingJobs ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading jobs…
                </div>
              ) : fetchJobs.length === 0 ? (
                <div className="flex items-center gap-2 text-sm text-amber-400">
                  <AlertTriangle className="w-4 h-4" />
                  No completed fetch jobs found. Run a Sunsky fetch first.
                </div>
              ) : (
                <select
                  value={fetchJobId}
                  onChange={(e) => setFetchJobId(e.target.value)}
                  className={inputCls}
                >
                  <option value="">— Select a fetch job —</option>
                  {fetchJobs.map((j) => (
                    <option key={j.id} value={j.id}>{jobLabel(j)}</option>
                  ))}
                </select>
              )}
            </div>
          )}

          {/* ── CSV import fields ── */}
          {productSource === "csv" && (
            <div className="mt-3">
              {!storeId ? (
                <p className="text-sm text-muted-foreground italic">Select a store first</p>
              ) : loadingJobs ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading imports…
                </div>
              ) : csvJobs.length === 0 ? (
                <div className="flex items-center gap-2 text-sm text-amber-400">
                  <AlertTriangle className="w-4 h-4" />
                  No CSV imports found. Upload a CSV file first.
                </div>
              ) : (
                <>
                  <select
                    value={fetchJobId}
                    onChange={(e) => setFetchJobId(e.target.value)}
                    className={inputCls}
                  >
                    <option value="">— Select a CSV import —</option>
                    {csvJobs.map((j) => (
                      <option key={j.id} value={j.id}>{jobLabel(j)}</option>
                    ))}
                  </select>
                  {selectedJob && (
                    <div className="mt-2 flex items-center gap-2 text-xs text-violet-400 bg-violet-500/10 border border-violet-500/20 rounded-lg px-3 py-2">
                      <FileText className="w-3.5 h-3.5 shrink-0" />
                      <span>CSV import — title and SKU come from the file.</span>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {/* Options */}
        <div className="space-y-3 pt-1">
          <div className="flex items-start gap-3 p-3 rounded-xl bg-secondary/30 border border-border/40">
            <Toggle checked={includeEnrich} onChange={setIncludeEnrich} />
            <div>
              <p className="text-sm font-medium flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-orange-400" />
                Include Attribute Enrichment
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Extract product attributes with AI, pause for review before continuing.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 p-3 rounded-xl bg-secondary/30 border border-border/40">
            <Toggle checked={includeGenerate} onChange={setIncludeGenerate} />
            <div>
              <p className="text-sm font-medium flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-violet-400" />
                Include Content Generation
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Run AI content generation before upload. Uses Content Generation settings.
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
                Re-process and re-upload already handled products. Updates in-place.
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
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5 mb-2">
                  <Cpu className="w-3 h-3 text-amber-400" /> Process
                </p>
                <label className="text-xs text-muted-foreground">Max products</label>
                <input type="number" value={processLimit} onChange={(e) => setProcessLimit(e.target.value)}
                  min={1} className={cn(inputCls, "mt-1")} />
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5 mb-2">
                  <Upload className="w-3 h-3 text-emerald-400" /> Upload
                </p>
                <label className="text-xs text-muted-foreground">Max products</label>
                <input type="number" value={uploadLimit} onChange={(e) => setUploadLimit(e.target.value)}
                  min={1} className={cn(inputCls, "mt-1 mb-2")} />
                <label className="flex items-center gap-2.5 cursor-pointer text-sm">
                  <input type="checkbox" checked={uploadSkipImages}
                    onChange={(e) => setUploadSkipImages(e.target.checked)}
                    className="w-4 h-4 rounded accent-primary" />
                  Skip image upload
                </label>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5 mb-2">
                  <ArrowRightLeft className="w-3 h-3 text-pink-400" /> Sync
                </p>
                <label className="text-xs text-muted-foreground">Max products</label>
                <input type="number" value={syncLimit} onChange={(e) => setSyncLimit(e.target.value)}
                  min={1} className={cn(inputCls, "mt-1 mb-2")} />
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
            </p>
          </div>
        </div>

        {/* Run button */}
        <button
          onClick={handleRun}
          disabled={isRunDisabled}
          className="w-full py-3 rounded-xl bg-primary text-primary-foreground font-medium shadow-lg shadow-primary/20 hover:shadow-primary/40 hover:-translate-y-0.5 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed disabled:translate-y-0 disabled:shadow-none"
        >
          {fetching ? (
            <><Loader2 className="w-4 h-4 animate-spin" /> Fetching from Sunsky…</>
          ) : running ? (
            <><Loader2 className="w-4 h-4 animate-spin" /> Starting pipeline…</>
          ) : (
            <><Play className="w-4 h-4 fill-current" /> Run Pipeline</>
          )}
        </button>
      </div>

      {/* What happens next */}
      <div className="bg-secondary/20 border border-border/30 rounded-2xl p-5 text-sm text-muted-foreground space-y-2">
        <p className="font-medium text-foreground text-xs uppercase tracking-wider flex items-center gap-1.5">
          <Info className="w-3.5 h-3.5 text-primary" /> What happens next
        </p>
        <ul className="space-y-1.5 list-disc list-inside text-xs leading-relaxed">
          {productSource === "sunsky" && <li><strong className="text-foreground">Fetch</strong> — products pulled from Sunsky API and saved to your database</li>}
          <li><strong className="text-foreground">Process</strong> — images downloaded, compressed, watermarked</li>
          {includeEnrich && <li><strong className="text-foreground">Enrich</strong> — AI extracts attributes; pauses for your review</li>}
          {includeGenerate && <li><strong className="text-foreground">Generate</strong> — AI content created for each product</li>}
          <li><strong className="text-foreground">Review</strong> — pipeline pauses; confirm category mappings before upload</li>
          <li><strong className="text-foreground">Upload</strong> — products pushed as drafts to WooCommerce</li>
          <li><strong className="text-foreground">Sync</strong> — categories and attributes assigned</li>
          <li>If another pipeline is running for this store, yours will be queued automatically</li>
        </ul>
      </div>
    </div>
  );
}
