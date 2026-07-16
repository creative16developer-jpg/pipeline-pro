import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useRoute, Link, useLocation } from "wouter";
import {
  ArrowLeft, CheckCircle2, XCircle, Loader2, Clock, Play,
  RotateCcw, Square, Activity, AlertTriangle, Check, X as XIcon,
  Tag, Layers, Upload, RefreshCw, ChevronDown, ChevronRight,
  Eye, Info, Terminal, Zap,
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

// ─────────────────────────────────────────────────────────────────────────────
// Stage Trail
// ─────────────────────────────────────────────────────────────────────────────

const STAGES: { key: string; label: string }[] = [
  { key: "fetch",         label: "Fetch" },
  { key: "process",       label: "Process" },
  { key: "enrich",        label: "Enrich" },
  { key: "enrich_review", label: "Attr. Review" },
  { key: "generate",      label: "Generate" },
  { key: "review",        label: "Cat. Review" },
  { key: "upload",        label: "Upload" },
  { key: "sync",          label: "Sync" },
];

function getActiveStageIndex(pl: Pipeline): number {
  if (pl.status === "completed") return STAGES.length;
  if (pl.status === "enrich_review") return 3;
  if (pl.status === "review" || pl.status === "category_review") return 5;
  const map: Record<string, number> = {
    fetch: 0, process: 1, enrich: 2, generate: 4, upload: 6, sync: 7,
  };
  return map[pl.current_step ?? "process"] ?? 1;
}

function StageTrail({ pl }: { pl: Pipeline }) {
  const activeIdx = getActiveStageIndex(pl);
  const isFailed = pl.status === "failed" || pl.status === "cancelled";

  return (
    <div className="flex flex-wrap items-center gap-y-2">
      {STAGES.map((stage, i) => {
        const isDone = i < activeIdx;
        const isActive = i === activeIdx && !isFailed && pl.status !== "completed" && pl.status !== "queued";
        const isFail = i === activeIdx && isFailed;

        return (
          <div key={stage.key} className="flex items-center">
            <div className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap border",
              isDone  && "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
              isActive && "bg-primary/15 text-primary border-primary/30",
              isFail  && "bg-red-500/10 text-red-400 border-red-500/20",
              !isDone && !isActive && !isFail && "bg-secondary/50 text-muted-foreground border-border/30",
            )}>
              {isDone   && <Check className="w-3 h-3" />}
              {isActive && <Loader2 className="w-3 h-3 animate-spin" />}
              {isFail   && <XIcon className="w-3 h-3" />}
              {!isDone && !isActive && !isFail && (
                <span className="w-3 h-3 flex items-center justify-center opacity-40 text-[10px]">◯</span>
              )}
              {stage.label}
            </div>
            {i < STAGES.length - 1 && (
              <div className={cn("w-4 h-px mx-0.5", isDone ? "bg-emerald-500/30" : "bg-border/30")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Log Panel
// ─────────────────────────────────────────────────────────────────────────────

const LOG_COLOR: Record<string, string> = {
  info: "text-foreground",
  warn: "text-amber-400",
  error: "text-red-400",
  ok: "text-emerald-400",
  debug: "text-muted-foreground",
};

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
    if (isLive) pollRef.current = setInterval(fetchLogs, 2500);
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
    <div className="max-h-72 overflow-y-auto bg-black/25 font-mono text-xs p-3 space-y-0.5 rounded-b-xl">
      {logs.map((log) => (
        <div key={log.id} className="flex gap-2">
          <span className="text-muted-foreground shrink-0 w-20">
            {log.created_at ? format(new Date(log.created_at), "HH:mm:ss") : ""}
          </span>
          {log.step && (
            <span className="text-primary/60 shrink-0 w-16 truncate">[{log.step}]</span>
          )}
          <span className={cn("uppercase font-bold w-10 shrink-0", LOG_COLOR[log.level] ?? "text-foreground")}>
            [{log.level}]
          </span>
          <span className={cn("break-all", LOG_COLOR[log.level] ?? "text-foreground")}>{log.message}</span>
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
// Running state — step jobs progress
// ─────────────────────────────────────────────────────────────────────────────

const STEP_LABELS: Record<string, string> = {
  process: "Processing products",
  enrich: "Extracting attributes",
  generate: "Generating content",
  review: "Category review",
  upload: "Uploading to WooCommerce",
  sync: "Syncing categories",
};

function RunningSection({ pl }: { pl: Pipeline }) {
  const currentStepLabel = STEP_LABELS[pl.current_step ?? ""] ?? pl.current_step ?? "Initialising…";
  const stepJobs = pl.step_jobs ?? [];
  const currentJob = stepJobs.find(j => j.status === "running");
  const pct = currentJob?.progress_percent ?? 0;
  const total = currentJob?.total_items ?? 0;
  const done = currentJob?.processed_items ?? 0;

  return (
    <div className="space-y-4">
      {/* Current stage card */}
      <div className="bg-primary/5 border border-primary/20 rounded-xl p-4">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-8 h-8 rounded-full bg-primary/15 flex items-center justify-center">
            <Loader2 className="w-4 h-4 text-primary animate-spin" />
          </div>
          <div>
            <p className="text-xs text-muted-foreground uppercase tracking-wider">Current Stage</p>
            <p className="font-semibold text-foreground">{currentStepLabel}</p>
          </div>
          <div className="ml-auto text-right">
            <p className="text-2xl font-bold font-display text-primary">{pct}%</p>
            {total > 0 && <p className="text-xs text-muted-foreground">{done.toLocaleString()} / {total.toLocaleString()} products</p>}
          </div>
        </div>
        {total > 0 && (
          <div className="w-full bg-secondary/50 rounded-full h-2">
            <div
              className="bg-primary h-2 rounded-full transition-all duration-500"
              style={{ width: `${Math.min(100, pct)}%` }}
            />
          </div>
        )}
      </div>

      {/* Step jobs */}
      {stepJobs.length > 0 && (
        <div className="space-y-2">
          {stepJobs.map((job) => (
            <div key={job.id} className={cn(
              "flex items-center gap-3 px-4 py-2.5 rounded-lg border text-sm",
              job.status === "running"  && "border-primary/20 bg-primary/5",
              job.status === "completed"&& "border-emerald-500/20 bg-emerald-500/5",
              job.status === "failed"   && "border-red-500/20 bg-red-500/5",
              job.status === "pending"  && "border-border/30 bg-secondary/20",
            )}>
              {job.status === "running"   && <Loader2 className="w-3.5 h-3.5 text-primary animate-spin shrink-0" />}
              {job.status === "completed" && <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0" />}
              {job.status === "failed"    && <XIcon className="w-3.5 h-3.5 text-red-400 shrink-0" />}
              {job.status === "pending"   && <span className="w-3.5 h-3.5 shrink-0 flex items-center justify-center text-muted-foreground text-[10px]">◯</span>}
              <span className={cn(
                "font-medium",
                job.status === "running"  && "text-primary",
                job.status === "completed"&& "text-emerald-400",
                job.status === "failed"   && "text-red-400",
                job.status === "pending"  && "text-muted-foreground",
              )}>
                {STEP_LABELS[job.type] ?? job.type}
              </span>
              {job.total_items > 0 && (
                <span className="text-xs text-muted-foreground ml-auto">
                  {job.processed_items.toLocaleString()} / {job.total_items.toLocaleString()}
                </span>
              )}
              {job.error_message && (
                <span className="text-xs text-red-400 truncate ml-auto">{job.error_message}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Enrich Review section (status = enrich_review)
// ─────────────────────────────────────────────────────────────────────────────

function EnrichReviewSection({ pl, onDone }: { pl: Pipeline; onDone: () => void }) {
  const { toast } = useToast();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [tab, setTab] = useState<"all" | "unset" | "ok">("all");

  useEffect(() => {
    fetch(`/api/pipelines/${pl.id}/enrich-data`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(setData)
      .catch(() => toast({ title: "Failed to load attribute data", variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [pl.id]);

  const products: any[] = useMemo(() => {
    const all = data?.products ?? [];
    if (tab === "unset") return all.filter((p: any) => p.attrs?.some((a: any) => !a.value || a.confidence === "low"));
    if (tab === "ok")    return all.filter((p: any) => p.attrs?.every((a: any) => a.value && a.confidence !== "low"));
    return all;
  }, [data, tab]);

  const totalProducts = data?.products?.length ?? 0;
  const needsReview   = (data?.products ?? []).filter((p: any) => p.attrs?.some((a: any) => !a.value || a.confidence === "low")).length;

  const handleConfirm = async () => {
    setSaving(true);
    try {
      const r = await fetch(`/api/pipelines/${pl.id}/enrich-confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolutions: {}, name_edits: {}, bulk_norm_edits: {} }),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Attribute extraction confirmed", description: "Pipeline continuing…" });
      onDone();
    } catch (e: any) {
      toast({ title: "Failed to confirm", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return (
    <div className="flex items-center gap-2 py-6 text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" /> Loading attribute data…
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="bg-orange-500/10 border border-orange-500/20 rounded-xl p-4">
        <div className="flex items-start gap-3">
          <Layers className="w-5 h-5 text-orange-400 mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold text-orange-300">Substep A — AI Attribute Extraction Review</p>
            <p className="text-sm text-orange-400/80 mt-0.5">
              The pipeline has extracted product attributes from Sunsky data. Review the results and confirm to continue.
            </p>
          </div>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: "Total Products", value: totalProducts, color: "" },
          { label: "Need Review", value: needsReview, color: needsReview > 0 ? "amber" : "emerald" },
          { label: "Ready", value: totalProducts - needsReview, color: "emerald" },
        ].map(s => (
          <div key={s.label} className="bg-card border border-border/50 rounded-xl p-4 text-center">
            <p className={cn("text-2xl font-bold font-display",
              s.color === "amber" ? "text-amber-400" :
              s.color === "emerald" ? "text-emerald-400" : "text-foreground"
            )}>{s.value}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Product list */}
      {totalProducts > 0 && (
        <div className="bg-card border border-border/50 rounded-xl overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border/50 bg-secondary/30">
            {(["all", "unset", "ok"] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "px-3 py-1 rounded-lg text-xs font-medium transition-colors",
                  tab === t ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground"
                )}
              >
                {t === "all" ? `All (${totalProducts})` :
                 t === "unset" ? `Needs Review (${needsReview})` :
                 `Ready (${totalProducts - needsReview})`}
              </button>
            ))}
          </div>
          <div className="max-h-72 overflow-y-auto divide-y divide-border/30">
            {products.slice(0, 50).map((p: any) => {
              const isExp = expanded.has(p.id);
              const hasIssues = p.attrs?.some((a: any) => !a.value || a.confidence === "low");
              return (
                <div key={p.id}>
                  <button
                    onClick={() => setExpanded(prev => { const s = new Set(prev); s.has(p.id) ? s.delete(p.id) : s.add(p.id); return s; })}
                    className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-secondary/10 text-left"
                  >
                    {isExp ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
                    <span className="font-mono text-xs text-muted-foreground shrink-0">{p.sku}</span>
                    <span className="text-sm flex-1 truncate">{p.name}</span>
                    {hasIssues ? (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 shrink-0">Review</span>
                    ) : (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 shrink-0">Ready</span>
                    )}
                  </button>
                  {isExp && p.attrs?.length > 0 && (
                    <div className="px-10 pb-3 flex flex-wrap gap-1.5">
                      {p.attrs.map((a: any, idx: number) => (
                        <span key={idx} className={cn(
                          "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border",
                          a.confidence === "high" ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" :
                          a.confidence === "low"  ? "bg-amber-500/10 text-amber-400 border-amber-500/20" :
                          !a.value               ? "bg-red-500/10 text-red-400 border-red-500/20" :
                          "bg-secondary text-muted-foreground border-border/30"
                        )}>
                          <span className="font-medium">{a.attribute}:</span>
                          <span>{a.value ?? "—"}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            {products.length > 50 && (
              <div className="px-4 py-2 text-xs text-muted-foreground italic">
                Showing 50 of {products.length} products — use All Runs view for full review.
              </div>
            )}
          </div>
        </div>
      )}

      {/* Confirm button */}
      <div className="flex items-center gap-3 pt-1">
        <button
          onClick={handleConfirm}
          disabled={saving}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-orange-500/10 hover:bg-orange-500/20 text-orange-400 border border-orange-500/20 font-medium transition-colors disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
          Confirm Extraction — Continue
        </button>
        <p className="text-xs text-muted-foreground">
          Accepts all extracted values as-is and proceeds to the next step.
        </p>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Category Review section (status = review / category_review)
// ─────────────────────────────────────────────────────────────────────────────

function CategoryReviewSection({ pl, onDone }: { pl: Pipeline; onDone: () => void }) {
  const { toast } = useToast();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [sel, setSel] = useState<Record<string, { woo_cat_id: number | null; profile_id: number | null; save_as_rule: boolean }>>({});
  const [search, setSearch] = useState<Record<string, string>>({});

  useEffect(() => {
    fetch(`/api/pipelines/${pl.id}/map-data`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => {
        setData(d);
        const init: Record<string, any> = {};
        (d.categories ?? []).forEach((c: any) => {
          const cats = c.woo_cats ?? [];
          init[c.sunsky_cat] = {
            woo_cat_id: c.primary_woo_cat_id ?? cats[0]?.id ?? null,
            profile_id: c.profile_id ?? null,
            save_as_rule: true,
          };
        });
        setSel(init);
      })
      .catch(() => toast({ title: "Failed to load category data", variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [pl.id]);

  // Build a flat list of WooCommerce categories with full path labels
  const wooOptions: { id: number; label: string }[] = useMemo(() => {
    const opts: any[] = data?.woo_options ?? [];
    const byId = new Map<number, any>(opts.map(o => [o.id, o]));
    function getPath(id: number): string {
      const parts: string[] = [];
      let cur = byId.get(id);
      while (cur) {
        parts.unshift(cur.name);
        cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
      }
      return parts.join(" › ");
    }
    return opts
      .map(o => ({ id: o.id, label: getPath(o.id) }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [data?.woo_options]);

  const profiles: any[] = data?.profiles ?? [];
  const cats: any[] = data?.categories ?? [];
  const newCats  = cats.filter(c => c.is_new);
  const knownCats = cats.filter(c => !c.is_new);

  const allAssigned = newCats.every(c => sel[c.sunsky_cat]?.woo_cat_id != null);

  const handleConfirm = async () => {
    setSaving(true);
    try {
      const mappings = cats
        .filter(c => sel[c.sunsky_cat]?.woo_cat_id != null || !c.is_new)
        .map(c => {
          const s = sel[c.sunsky_cat];
          const woo_cat_id = s?.woo_cat_id ?? c.woo_cats?.[0]?.id ?? null;
          return {
            sunsky_cat: c.sunsky_cat,
            woo_cats: woo_cat_id ? [{ id: woo_cat_id, name: wooOptions.find(o => o.id === woo_cat_id)?.label ?? "" }] : c.woo_cats ?? [],
            primary_woo_cat_id: woo_cat_id,
            profile_id: s?.profile_id ?? c.profile_id ?? null,
            save_as_rule: s?.save_as_rule ?? true,
          };
        });

      const r = await fetch(`/api/pipelines/${pl.id}/map-confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mappings }),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Categories confirmed", description: "Upload step starting…" });
      onDone();
    } catch (e: any) {
      toast({ title: "Failed to confirm", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return (
    <div className="flex items-center gap-2 py-6 text-muted-foreground">
      <Loader2 className="w-4 h-4 animate-spin" /> Loading category data…
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="bg-amber-500/10 border border-amber-500/20 rounded-xl p-4">
        <div className="flex items-start gap-3">
          <Tag className="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold text-amber-300">Category Mapping Review</p>
            <p className="text-sm text-amber-400/80 mt-0.5">
              Assign WooCommerce categories to each Sunsky product category. Known categories are pre-filled.
            </p>
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: "Auto-Mapped", value: knownCats.length, color: "emerald" },
          { label: "New Assigned", value: newCats.filter(c => sel[c.sunsky_cat]?.woo_cat_id != null).length + "/" + newCats.length, color: "blue" },
          { label: "Total Products", value: cats.reduce((s: number, c: any) => s + (c.product_count ?? 0), 0), color: "" },
        ].map(s => (
          <div key={s.label} className="bg-card border border-border/50 rounded-xl p-4 text-center">
            <p className={cn("text-2xl font-bold font-display",
              s.color === "emerald" ? "text-emerald-400" :
              s.color === "blue" ? "text-blue-400" : "text-foreground"
            )}>{s.value}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Already mapped */}
      {knownCats.length > 0 && (
        <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl p-3">
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            <span className="text-sm font-medium text-emerald-400">Already Mapped ({knownCats.length})</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {knownCats.map((c: any) => (
              <span key={c.sunsky_cat} className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                {c.sunsky_cat} → {c.woo_cats?.[0]?.name ?? "?"}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* New categories to map */}
      {newCats.length > 0 && (
        <div className="space-y-3">
          <p className="text-sm font-medium text-foreground">{newCats.length} new categor{newCats.length === 1 ? "y" : "ies"} to assign:</p>
          {newCats.map((c: any) => {
            const s = sel[c.sunsky_cat] ?? { woo_cat_id: null, profile_id: null, save_as_rule: true };
            const filterText = search[c.sunsky_cat] ?? "";
            const filtered = filterText
              ? wooOptions.filter(o => o.label.toLowerCase().includes(filterText.toLowerCase())).slice(0, 20)
              : wooOptions.slice(0, 100);

            return (
              <div key={c.sunsky_cat} className="bg-card border border-border/50 rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-foreground">{c.sunsky_cat}</p>
                    <p className="text-xs text-muted-foreground">{c.product_count} product{c.product_count !== 1 ? "s" : ""}</p>
                  </div>
                  {s.woo_cat_id ? (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                      ✓ Assigned
                    </span>
                  ) : (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">
                      Needs mapping
                    </span>
                  )}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {/* WooCommerce category */}
                  <div>
                    <label className="text-xs text-muted-foreground block mb-1">WooCommerce Category</label>
                    <input
                      type="text"
                      placeholder="Search categories…"
                      value={filterText}
                      onChange={e => setSearch(prev => ({ ...prev, [c.sunsky_cat]: e.target.value }))}
                      className="w-full px-3 py-1.5 text-xs rounded-lg bg-secondary border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 mb-1"
                    />
                    <select
                      value={s.woo_cat_id ?? ""}
                      onChange={e => setSel(prev => ({
                        ...prev,
                        [c.sunsky_cat]: { ...s, woo_cat_id: e.target.value ? parseInt(e.target.value) : null }
                      }))}
                      className="w-full px-3 py-1.5 text-xs rounded-lg bg-secondary border border-border/50 text-foreground focus:outline-none focus:border-primary/50"
                      size={Math.min(5, filtered.length + 1)}
                    >
                      <option value="">— No category —</option>
                      {filtered.map(o => (
                        <option key={o.id} value={o.id}>{o.label}</option>
                      ))}
                    </select>
                  </div>

                  {/* Attribute profile */}
                  <div>
                    <label className="text-xs text-muted-foreground block mb-1">Attribute Profile (optional)</label>
                    <select
                      value={s.profile_id ?? ""}
                      onChange={e => setSel(prev => ({
                        ...prev,
                        [c.sunsky_cat]: { ...s, profile_id: e.target.value ? parseInt(e.target.value) : null }
                      }))}
                      className="w-full px-3 py-1.5 text-xs rounded-lg bg-secondary border border-border/50 text-foreground focus:outline-none focus:border-primary/50"
                    >
                      <option value="">— No profile —</option>
                      {profiles.map((p: any) => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                    {/* Save rule checkbox */}
                    <label className="flex items-center gap-2 mt-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={s.save_as_rule}
                        onChange={e => setSel(prev => ({ ...prev, [c.sunsky_cat]: { ...s, save_as_rule: e.target.checked } }))}
                        className="accent-primary"
                      />
                      <span className="text-xs text-muted-foreground">Save as permanent rule</span>
                    </label>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Confirm button */}
      <div className="flex items-center gap-3 pt-1">
        <button
          onClick={handleConfirm}
          disabled={saving}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-amber-500/20 font-medium transition-colors disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
          Confirm &amp; Continue
        </button>
        {!allAssigned && newCats.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {newCats.filter(c => sel[c.sunsky_cat]?.woo_cat_id != null).length}/{newCats.length} new categories assigned
          </p>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Completed section
// ─────────────────────────────────────────────────────────────────────────────

function CompletedSection({ pl }: { pl: Pipeline }) {
  const stats = pl.stats_json ?? {};
  const uploaded = stats.uploaded ?? stats.ok ?? 0;
  const failed   = stats.failed ?? 0;
  const excluded = stats.excluded ?? stats.skipped ?? 0;
  const total    = stats.total ?? (uploaded + failed + excluded);

  return (
    <div className="space-y-4">
      <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-xl p-4">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="w-5 h-5 text-emerald-400 mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold text-emerald-300">Pipeline Completed Successfully</p>
            <p className="text-sm text-emerald-400/80 mt-0.5">
              All products have been processed and uploaded to WooCommerce as drafts.
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {[
          { label: "Uploaded", value: uploaded, color: "emerald", icon: Upload },
          { label: "Failed",   value: failed,   color: failed > 0 ? "rose" : "muted", icon: XCircle },
          { label: "Excluded", value: excluded, color: "amber",  icon: XIcon },
        ].map(s => (
          <div key={s.label} className="bg-card border border-border/50 rounded-xl p-5 text-center">
            <p className={cn("text-3xl font-bold font-display mb-1",
              s.color === "emerald" ? "text-emerald-400" :
              s.color === "rose"    ? "text-rose-400" :
              s.color === "amber"   ? "text-amber-400" :
              "text-muted-foreground"
            )}>{s.value}</p>
            <p className="text-sm text-muted-foreground">{s.label}</p>
          </div>
        ))}
      </div>

      {total > 0 && (
        <div>
          <div className="flex justify-between text-xs text-muted-foreground mb-1">
            <span>Upload success rate</span>
            <span>{total > 0 ? Math.round((uploaded / total) * 100) : 0}%</span>
          </div>
          <div className="w-full bg-secondary/50 rounded-full h-2 flex overflow-hidden">
            <div className="bg-emerald-500 h-2 transition-all" style={{ width: `${total > 0 ? (uploaded / total) * 100 : 0}%` }} />
            <div className="bg-rose-500 h-2 transition-all"   style={{ width: `${total > 0 ? (failed / total) * 100 : 0}%` }} />
            <div className="bg-amber-500 h-2 transition-all"  style={{ width: `${total > 0 ? (excluded / total) * 100 : 0}%` }} />
          </div>
        </div>
      )}

      {failed > 0 && (
        <div className="bg-rose-500/5 border border-rose-500/20 rounded-xl p-4">
          <p className="text-sm text-rose-400 font-medium mb-2 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {failed} product{failed !== 1 ? "s" : ""} failed to upload
          </p>
          <p className="text-xs text-muted-foreground">
            Review the log below for details on failed uploads. You can retry the pipeline to attempt re-uploading.
          </p>
        </div>
      )}

      <div className="flex items-center gap-3 pt-1">
        <Link href="/products">
          <button className="flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 font-medium text-sm transition-colors">
            <Eye className="w-4 h-4" /> View Products
          </button>
        </Link>
        <Link href="/pipeline">
          <button className="flex items-center gap-2 px-4 py-2 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary border border-primary/20 font-medium text-sm transition-colors">
            <Zap className="w-4 h-4" /> New Pipeline
          </button>
        </Link>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Failed / Cancelled section
// ─────────────────────────────────────────────────────────────────────────────

function FailedSection({ pl, onAction }: { pl: Pipeline; onAction: (action: string) => void }) {
  return (
    <div className="space-y-4">
      <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4">
        <div className="flex items-start gap-3">
          <XCircle className="w-5 h-5 text-red-400 mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold text-red-300">
              Pipeline {pl.status === "cancelled" ? "Cancelled" : "Failed"}
            </p>
            {pl.error_message && (
              <p className="text-sm text-red-400/80 mt-1 font-mono">{pl.error_message}</p>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={() => onAction("continue")}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 font-medium text-sm transition-colors"
        >
          <Play className="w-4 h-4 fill-current" /> Continue from last step
        </button>
        <button
          onClick={() => onAction("retry")}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-secondary hover:bg-secondary/80 text-muted-foreground font-medium text-sm transition-colors"
        >
          <RotateCcw className="w-4 h-4" /> Retry from scratch
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Page
// ─────────────────────────────────────────────────────────────────────────────

export default function PipelineDetail() {
  const [, params] = useRoute("/pipelines/:id");
  const [, navigate] = useLocation();
  const { toast } = useToast();
  const { data: stores } = useStores();

  const plId = parseInt(params?.id ?? "0");
  const [pl, setPl] = useState<Pipeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [logOpen, setLogOpen] = useState(true);

  const storeMap = Object.fromEntries((stores ?? []).map((s) => [s.id, s.name]));

  const fetchPipeline = useCallback(async () => {
    try {
      const r = await fetch(`/api/pipelines/${plId}`);
      if (!r.ok) throw new Error(`Pipeline not found (${r.status})`);
      const d = await r.json();
      setPl(d);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [plId]);

  const isLive = pl ? ["running", "queued"].includes(pl.status) : false;
  const isReview = pl ? ["review", "enrich_review", "category_review"].includes(pl.status) : false;

  useEffect(() => {
    fetchPipeline();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [fetchPipeline]);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (isLive || isReview) {
      pollRef.current = setInterval(fetchPipeline, 3000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [isLive, isReview, fetchPipeline]);

  const handleAction = async (action: string) => {
    try {
      const r = await fetch(`/api/pipelines/${plId}/${action}`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      if (action === "retry") {
        toast({ title: "Retry started", description: `New pipeline ${d.pl_id} created` });
        navigate(`/pipelines/${d.id}`);
      } else if (action === "cancel") {
        toast({ title: "Pipeline cancelled" });
        fetchPipeline();
      } else {
        toast({ title: "Action completed" });
        fetchPipeline();
      }
    } catch (e: any) {
      toast({ title: "Action failed", description: e.message, variant: "destructive" });
    }
  };

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <Loader2 className="w-8 h-8 animate-spin text-primary" />
    </div>
  );

  if (error || !pl) return (
    <div className="space-y-4">
      <Link href="/pipelines">
        <button className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="w-4 h-4" /> All Runs
        </button>
      </Link>
      <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-6 text-red-400">
        {error ?? "Pipeline not found"}
      </div>
    </div>
  );

  const storeName = storeMap[pl.store_id];
  const storeColor = getStoreColor(pl.store_id);

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2">
        <Link href="/pipelines">
          <button className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
            <ArrowLeft className="w-4 h-4" /> All Runs
          </button>
        </Link>
        <span className="text-border">/</span>
        <span className="text-sm font-mono text-foreground font-semibold">{pl.pl_id}</span>
      </div>

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-4 justify-between">
        <div className="space-y-2">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-display font-bold">{pl.pl_id}</h1>
            <span className={cn(
              "inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-sm font-medium border",
              pl.status === "running"       && "bg-primary/10 text-primary border-primary/25",
              pl.status === "enrich_review" && "bg-orange-500/10 text-orange-400 border-orange-500/25",
              pl.status === "review"        && "bg-amber-500/10 text-amber-400 border-amber-500/25",
              pl.status === "category_review"&& "bg-amber-500/10 text-amber-400 border-amber-500/25",
              pl.status === "completed"     && "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",
              pl.status === "failed"        && "bg-red-500/10 text-red-400 border-red-500/25",
              pl.status === "cancelled"     && "bg-zinc-500/10 text-zinc-400 border-zinc-500/25",
              pl.status === "queued"        && "bg-secondary text-muted-foreground border-border",
            )}>
              {pl.status === "running"       && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {pl.status === "enrich_review" && <span className="w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse" />}
              {pl.status === "review"        && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />}
              {pl.status === "category_review"&&<span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />}
              {pl.status === "completed"     && <CheckCircle2 className="w-3.5 h-3.5" />}
              {pl.status === "failed"        && <XCircle className="w-3.5 h-3.5" />}
              {(pl.status === "failed" || pl.status === "cancelled")
                ? pl.status.charAt(0).toUpperCase() + pl.status.slice(1)
                : pl.status === "running"
                  ? "Running"
                  : pl.status === "enrich_review"
                    ? "Attribute Review"
                    : pl.status === "review" || pl.status === "category_review"
                      ? "Category Review"
                      : pl.status === "completed"
                        ? "Completed"
                        : pl.status.charAt(0).toUpperCase() + pl.status.slice(1)
              }
            </span>
          </div>
          <div className="flex items-center gap-3 text-sm text-muted-foreground flex-wrap">
            <span className={cn("flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium border", storeColor.bg, storeColor.text, storeColor.border)}>
              <span className={cn("w-1.5 h-1.5 rounded-full", storeColor.dot)} />
              {storeName ?? `Store #${pl.store_id}`}
            </span>
            <span>Fetch Job #{pl.fetch_job_id}</span>
            <span>Started {pl.created_at ? format(new Date(pl.created_at), "MMM d, yyyy HH:mm") : "—"}</span>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {(isLive || isReview) && (
            <button
              onClick={() => handleAction("cancel")}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 text-sm font-medium transition-colors"
            >
              <Square className="w-3.5 h-3.5 fill-current" /> Cancel
            </button>
          )}
          <button
            onClick={fetchPipeline}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-secondary hover:bg-secondary/80 text-muted-foreground text-sm font-medium transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
        </div>
      </div>

      {/* Stage Trail */}
      <div className="bg-card border border-border/50 rounded-2xl p-4 shadow-sm">
        <p className="text-xs text-muted-foreground uppercase tracking-wider mb-3">Pipeline Stages</p>
        <StageTrail pl={pl} />
      </div>

      {/* State-specific content */}
      <div className="bg-card border border-border/50 rounded-2xl p-6 shadow-sm">
        {pl.status === "running" && (
          <>
            <h2 className="text-lg font-semibold mb-4">Pipeline Progress</h2>
            <RunningSection pl={pl} />
          </>
        )}

        {pl.status === "queued" && (
          <div className="flex items-start gap-3 py-4">
            <Clock className="w-5 h-5 text-muted-foreground mt-0.5 shrink-0" />
            <div>
              <p className="font-semibold">Queued</p>
              <p className="text-sm text-muted-foreground mt-0.5">
                This pipeline is waiting for another pipeline on the same store to complete.
                It will start automatically.
              </p>
            </div>
          </div>
        )}

        {pl.status === "enrich_review" && (
          <>
            <h2 className="text-lg font-semibold mb-4">Attribute Extraction Review</h2>
            <EnrichReviewSection pl={pl} onDone={fetchPipeline} />
          </>
        )}

        {(pl.status === "review" || pl.status === "category_review") && (
          <>
            <h2 className="text-lg font-semibold mb-4">Category Mapping</h2>
            <CategoryReviewSection pl={pl} onDone={fetchPipeline} />
          </>
        )}

        {pl.status === "completed" && (
          <>
            <h2 className="text-lg font-semibold mb-4">Results</h2>
            <CompletedSection pl={pl} />
          </>
        )}

        {(pl.status === "failed" || pl.status === "cancelled") && (
          <>
            <h2 className="text-lg font-semibold mb-4">Pipeline Ended</h2>
            <FailedSection pl={pl} onAction={handleAction} />
          </>
        )}
      </div>

      {/* Log Panel */}
      <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-sm">
        <button
          onClick={() => setLogOpen(x => !x)}
          className="w-full flex items-center gap-2 px-4 py-3 border-b border-border/50 hover:bg-secondary/10 transition-colors text-left"
        >
          <Terminal className="w-4 h-4 text-muted-foreground" />
          <span className="text-sm font-medium">Pipeline Log</span>
          {isLive && (
            <span className="flex items-center gap-1 text-xs text-primary ml-2">
              <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" /> Live
            </span>
          )}
          <span className="ml-auto text-muted-foreground">{logOpen ? "▲" : "▼"}</span>
        </button>
        {logOpen && <LogPanel plId={plId} isLive={isLive || isReview} />}
      </div>
    </div>
  );
}
