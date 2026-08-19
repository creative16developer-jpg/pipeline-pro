import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useRoute, Link, useLocation } from "wouter";
import {
  ArrowLeft, CheckCircle2, XCircle, Loader2, Clock, Play,
  RotateCcw, Square, RefreshCw, AlertTriangle, Check, X as XIcon,
  Upload, Eye, Terminal, Zap, ChevronDown, ChevronRight, Trash2, Plus, Sparkles,
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
// Stage Trail  (matches prototype: Fetch→Process→Enrich→Generate→Cat.Review→Review→Upload→Sync)
// ─────────────────────────────────────────────────────────────────────────────

const STAGES = [
  { key: "fetch",      label: "Fetch" },
  { key: "process",    label: "Process" },
  { key: "enrich",     label: "Enrich" },
  { key: "generate",   label: "Generate" },
  { key: "cat_review", label: "Cat. Review" },
  { key: "review",     label: "Review" },
  { key: "upload",     label: "Upload" },
  { key: "sync",       label: "Sync" },
];

function getActiveStageIndex(pl: Pipeline): number {
  if (pl.status === "completed") return 8;
  if (pl.status === "enrich_review") return 2;
  if (pl.status === "review") return 4;
  if (pl.status === "content_review") return 5;
  if (pl.status === "queued" || pl.status === "failed" || pl.status === "cancelled") return -1;
  const stepMap: Record<string, number> = {
    fetch: 0, process: 1, enrich: 2, generate: 3,
    upload: 6, sync: 7,
  };
  return stepMap[pl.current_step ?? "process"] ?? 1;
}

function StageTrail({ pl }: { pl: Pipeline }) {
  const activeIdx = getActiveStageIndex(pl);
  const isPaused  = ["enrich_review","review","content_review"].includes(pl.status);
  const isFailed  = ["failed","cancelled"].includes(pl.status);

  return (
    <div className="flex flex-wrap items-center gap-y-2">
      {STAGES.map((stage, i) => {
        const isDone   = activeIdx >= 0 && i < activeIdx;
        const isActive = activeIdx >= 0 && i === activeIdx && !isFailed;
        const isFail   = isFailed && i === activeIdx;

        return (
          <div key={stage.key} className="flex items-center">
            <span className={cn(
              "inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium border whitespace-nowrap",
              isDone   && "bg-emerald-500/15 border-emerald-500/30 text-emerald-400",
              isActive && isPaused  && "bg-amber-500/15 border-amber-500/40 text-amber-300 font-semibold",
              isActive && !isPaused && "bg-violet-500/15 border-violet-500/40 text-violet-300 font-semibold",
              isFail   && "bg-red-500/15 border-red-500/30 text-red-400",
              !isDone && !isActive && !isFail && "border-border/40 text-muted-foreground/60 bg-background",
            )}>
              {isDone   && "✓ "}
              {isActive && isPaused  && "⏸ "}
              {isActive && !isPaused && "▶ "}
              {stage.label}
            </span>
            {i < STAGES.length - 1 && (
              <span className="text-muted-foreground/30 mx-1 text-xs">→</span>
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

const LOG_STYLE: Record<string, string> = {
  ok:    "text-emerald-400",
  warn:  "text-amber-400",
  error: "text-red-400",
  info:  "text-foreground/80",
  debug: "text-muted-foreground",
};

function LogPanel({ plId, isLive }: { plId: number; isLive: boolean }) {
  const [logs, setLogs]   = useState<PipelineLog[]>([]);
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pollRef   = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchLogs = useCallback(async () => {
    try {
      const r = await fetch(`/api/pipelines/${plId}/logs?limit=300`);
      if (r.ok) { const d = await r.json(); setLogs(d.logs ?? []); }
    } catch { /* ignore */ }
    setLoading(false);
  }, [plId]);

  useEffect(() => {
    fetchLogs();
    if (isLive) pollRef.current = setInterval(fetchLogs, 2500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [fetchLogs, isLive]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs.length]);

  if (loading) return <div className="p-4 text-sm text-muted-foreground flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading logs…</div>;
  if (logs.length === 0) return <div className="p-4 text-sm text-muted-foreground italic">No log entries yet.</div>;

  return (
    <div className="max-h-[150px] overflow-y-auto bg-background font-mono text-[12px] p-3 space-y-0.5">
      {logs.map(log => (
        <div key={log.id} className="flex gap-2">
          <span className="text-muted-foreground/60 shrink-0">{log.created_at ? format(new Date(log.created_at), "HH:mm:ss") : ""}</span>
          {log.level === "ok"   && <span className="text-emerald-400 shrink-0">✓</span>}
          {log.level === "warn" && <span className="text-amber-500 shrink-0">⚠</span>}
          <span className={cn("break-all", LOG_STYLE[log.level] ?? "")}>{log.message}</span>
        </div>
      ))}
      {isLive && <div className="flex items-center gap-1 text-violet-500 mt-1"><span className="w-1.5 h-1.5 bg-violet-500 rounded-full animate-pulse inline-block" /> Live</div>}
      <div ref={bottomRef} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Running state — 2-column Progress + Current Stage (matches prototype)
// ─────────────────────────────────────────────────────────────────────────────

const STAGE_DESCRIPTIONS: Record<string, string> = {
  fetch:   "Fetching product list and data from Sunsky API.",
  process: "Downloading images, resizing, converting to WebP.",
  enrich:  "Reading Sunsky title + spec block.\nExtracting attributes via AI.\nConfidence threshold: 70%",
  generate:"Running AI content generation for titles, descriptions, and attributes.",
  upload:  "Uploading products to WooCommerce as drafts.",
  sync:    "Syncing WooCommerce categories and attributes.",
};

function RunningSection({ pl }: { pl: Pipeline }) {
  const stepJobs   = pl.step_jobs ?? [];
  const currentJob = stepJobs.find(j => j.status === "running");
  const pct   = currentJob?.progress_percent ?? 0;
  const total = currentJob?.total_items ?? 0;
  const done  = currentJob?.processed_items ?? 0;
  const step  = pl.current_step ?? "";

  const stageLabel = {
    fetch: "Fetching from Sunsky", process: "Processing Images",
    enrich: "AI Attribute Extraction", generate: "Generating Content",
    upload: "Uploading to WooCommerce", sync: "Syncing Categories",
  }[step] ?? step;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* PROGRESS */}
        <div className="bg-card border border-border rounded-[10px] p-5">
          <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.6px] mb-3">Progress</div>
          <div className="text-[30px] font-bold text-foreground leading-none tracking-[-1px] mb-2">
            {total > 0 ? done : "—"}{" "}
            <span className="text-[15px] font-normal text-muted-foreground/60">
              {total > 0 ? `/ ${total} products` : "products"}
            </span>
          </div>
          <div className="bg-muted/50 rounded-full h-1.5 overflow-hidden mb-2">
            <div className="h-full bg-violet-500 rounded-full transition-all duration-500" style={{ width: `${Math.min(100, pct)}%` }} />
          </div>
          <div className="text-[12px] text-muted-foreground">
            {pct}% · {stageLabel}{total > 0 ? "" : " · starting…"}
          </div>
        </div>

        {/* CURRENT STAGE */}
        <div className="bg-card border border-border rounded-[10px] p-5">
          <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.6px] mb-3">Current Stage</div>
          <div className="text-[14px] font-semibold text-violet-400 mb-2">{stageLabel}</div>
          <div className="text-[13px] text-muted-foreground whitespace-pre-line leading-[1.7]">
            {STAGE_DESCRIPTIONS[step] ?? "Processing…"}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Enrich Review — Substep A (status = enrich_review)
// ─────────────────────────────────────────────────────────────────────────────

function EnrichReviewSection({ pl, onDone }: { pl: Pipeline; onDone: () => void }) {
  const { toast } = useToast();
  const [data, setData]     = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [tab, setTab]         = useState<"all"|"review"|"ok">("all");

  useEffect(() => {
    fetch(`/api/pipelines/${pl.id}/enrich-data`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(setData)
      .catch(() => toast({ title: "Failed to load attribute data", variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [pl.id]);

  const allProducts: any[] = data?.products ?? [];
  const needsReview = allProducts.filter((p: any) => p.attrs?.some((a: any) => a.status === "unset" || a.status === "low_confidence")).length;
  const ready       = allProducts.length - needsReview;

  const displayed = useMemo(() => {
    if (tab === "review") return allProducts.filter((p: any) => p.attrs?.some((a: any) => a.status === "unset" || a.status === "low_confidence"));
    if (tab === "ok")     return allProducts.filter((p: any) => p.attrs?.every((a: any) => a.status === "resolved"));
    return allProducts;
  }, [allProducts, tab]);

  const handleConfirm = async () => {
    setSaving(true);
    try {
      const r = await fetch(`/api/pipelines/${pl.id}/enrich-confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolutions: {}, name_edits: {}, bulk_norm_edits: {} }),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Extraction confirmed", description: "Pipeline continuing…" });
      onDone();
    } catch (e: any) {
      toast({ title: "Failed to confirm", description: e.message, variant: "destructive" });
    } finally { setSaving(false); }
  };

  if (loading) return <div className="flex items-center gap-2 py-8 text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  return (
    <div className="space-y-4">
      {/* Substep A card */}
      <div className="bg-card border border-violet-500/30 border-l-[3px] border-l-violet-500 rounded-[10px] p-5">
        <div className="flex items-center gap-3 mb-2 flex-wrap">
          <span className="w-[22px] h-[22px] rounded-full bg-violet-500/20 text-violet-400 flex items-center justify-center text-[12px] font-bold flex-shrink-0">1</span>
          <div className="text-[13px] font-semibold text-violet-400">Substep A — AI extracts attribute values from raw Sunsky data</div>
          <div className="ml-auto flex gap-2 flex-shrink-0 flex-wrap">
            {needsReview > 0 && <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/15 text-amber-400">{needsReview} need review</span>}
            {ready > 0       && <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/15 text-emerald-400">{ready} auto-confirmed</span>}
          </div>
        </div>
        <div className="text-[12px] text-muted-foreground mb-4">AI reads raw Sunsky title + spec block and extracts structured values. You confirm or correct.</div>

        {/* Legend */}
        <div className="flex gap-4 text-[12px] text-muted-foreground mb-4 flex-wrap">
          <span className="flex items-center gap-1.5">
            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-400">high confidence</span>
            auto-confirmed, editable
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-amber-500/15 border border-amber-500/30 text-amber-400">low confidence</span>
            needs your review
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-red-500/15 border border-red-500/30 text-red-400">missing</span>
            AI could not extract — set manually
          </span>
        </div>

        {/* Tab bar */}
        {allProducts.length > 0 && (
          <div className="flex gap-1 bg-muted/50 rounded-lg p-1 mb-4 w-fit">
            {(["all","review","ok"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={cn("px-3 py-1.5 rounded-md text-[13px] font-medium transition-colors",
                  tab === t ? "bg-muted text-violet-400 shadow-sm" : "text-muted-foreground hover:text-foreground"
                )}>
                {t === "all" ? `All (${allProducts.length})` : t === "review" ? `Needs attention (${needsReview})` : `Ready (${ready})`}
              </button>
            ))}
          </div>
        )}

        {/* Products table */}
        {allProducts.length > 0 ? (
          <div className="rounded-lg border border-border overflow-hidden mb-4">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="bg-background">
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.5px] border-b border-border">Product</th>
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.5px] border-b border-border">Extracted Attributes</th>
                </tr>
              </thead>
              <tbody>
                {displayed.slice(0, 30).map((p: any) => (
                  <tr key={p.id} className="border-b border-border/20 last:border-0 hover:bg-card/50">
                    <td className="px-4 py-3 align-top">
                      <div className="font-medium text-foreground">{p.name}</div>
                      <div className="text-[12px] text-muted-foreground font-mono">{p.sku}</div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-wrap gap-1.5">
                        {(p.attrs ?? []).map((a: any, idx: number) => (
                          <span key={idx} className={cn(
                            "inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[12px] font-medium border-[1.5px]",
                            a.status === "resolved"       ? "bg-emerald-500/15 border-emerald-500/30 text-emerald-400" :
                            a.status === "low_confidence" ? "bg-amber-500/15 border-amber-500/30 text-amber-400" :
                            a.status === "unset"          ? "bg-red-500/15 border-red-500/30 text-red-400" :
                            "bg-muted/50 border-border text-foreground/70"
                          )}>
                            {a.attribute} : {a.raw_value || "not found"}
                            {typeof a.confidence === "number" && a.status === "low_confidence" && (
                              <span className="text-[11px] ml-0.5 text-amber-400">
                                {` ${Math.round(a.confidence * 100)}%`}
                              </span>
                            )}
                          </span>
                        ))}
                        {(!p.attrs || p.attrs.length === 0) && (
                          <span className="text-[12px] text-muted-foreground italic">No attributes extracted</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {displayed.length > 30 && (
                  <tr><td colSpan={2} className="px-4 py-2 text-[12px] text-muted-foreground italic">Showing 30 of {displayed.length} products</td></tr>
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-[13px] text-muted-foreground mb-4">No attribute data available yet.</div>
        )}

        {/* Bottom bar */}
        <div className="flex items-center justify-between pt-3 border-t border-border">
          <div className="text-[12px] text-muted-foreground">High-confidence extractions auto-confirm on next run if the same raw value is seen again</div>
          <button onClick={handleConfirm} disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-[13px] font-medium transition-colors disabled:opacity-50">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5 fill-current" />}
            Confirm extraction — continue ›
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Category Review — "Category Assignment Required" (status = review)
// ─────────────────────────────────────────────────────────────────────────────

function CategoryReviewSection({ pl, onDone }: { pl: Pipeline; onDone: () => void }) {
  const { toast } = useToast();
  const [data, setData]     = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [sel, setSel]         = useState<Record<string, { woo_cat_id: number|null; profile_id: number|null; save_as_rule: boolean }>>({});
  const [newCatForm, setNewCatForm] = useState<Record<string, { open: boolean; name: string; saving: boolean }>>({});

  const openNewCatForm = (sunsky_cat: string) =>
    setNewCatForm(f => ({ ...f, [sunsky_cat]: { open: true, name: "", saving: false } }));

  const handleCreateCategory = async (sunsky_cat: string, storeId: number) => {
    const form = newCatForm[sunsky_cat];
    if (!form?.name.trim()) return;
    setNewCatForm(f => ({ ...f, [sunsky_cat]: { ...f[sunsky_cat], saving: true } }));
    try {
      const r = await fetch(`/api/stores/${storeId}/categories/new`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: form.name.trim(), parent_woo_id: 0 }),
      });
      if (!r.ok) throw new Error(await r.text());
      const created = await r.json();
      setData((prev: any) => ({
        ...prev,
        woo_options: [...(prev?.woo_options ?? []), { id: created.id, woo_id: created.woo_id, name: created.name, parent_id: null }],
      }));
      setSel(s => ({ ...s, [sunsky_cat]: { ...(s[sunsky_cat] ?? {}), woo_cat_id: created.id } as any }));
      setNewCatForm(f => ({ ...f, [sunsky_cat]: { open: false, name: "", saving: false } }));
      toast({ title: `Category "${created.name}" created and selected` });
    } catch (e: any) {
      toast({ title: "Failed to create category", description: e.message, variant: "destructive" });
      setNewCatForm(f => ({ ...f, [sunsky_cat]: { ...f[sunsky_cat], saving: false } }));
    }
  };

  useEffect(() => {
    fetch(`/api/pipelines/${pl.id}/map-data`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => {
        setData(d);
        const init: Record<string, any> = {};
        (d.categories ?? []).forEach((c: any) => {
          init[c.sunsky_cat] = {
            woo_cat_id: c.primary_woo_cat_id ?? c.woo_cats?.[0]?.id ?? null,
            profile_id: c.profile_id ?? null,
            save_as_rule: true,
          };
        });
        setSel(init);
      })
      .catch(() => toast({ title: "Failed to load category data", variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [pl.id]);

  const wooOptions: { id: number; label: string }[] = useMemo(() => {
    const opts: any[] = data?.woo_options ?? [];
    const byId = new Map<number, any>(opts.map(o => [o.id, o]));
    function getPath(id: number): string {
      const parts: string[] = [];
      let cur = byId.get(id);
      while (cur) { parts.unshift(cur.name); cur = cur.parent_id ? byId.get(cur.parent_id) : undefined; }
      return parts.join(" / ");
    }
    return opts.map(o => ({ id: o.id, label: getPath(o.id) })).sort((a, b) => a.label.localeCompare(b.label));
  }, [data?.woo_options]);

  const profiles: any[]   = data?.profiles ?? [];
  const cats: any[]       = data?.categories ?? [];
  const newCats           = cats.filter(c => c.is_new);
  const knownCats         = cats.filter(c => !c.is_new);

  const handleConfirm = async () => {
    setSaving(true);
    try {
      const mappings = cats.map(c => {
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
      toast({ title: "Category mapping confirmed", description: "Proceeding to content review…" });
      onDone();
    } catch (e: any) {
      toast({ title: "Failed to confirm", description: e.message, variant: "destructive" });
    } finally { setSaving(false); }
  };

  if (loading) return <div className="flex items-center gap-2 py-8 text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  return (
    <div className="space-y-4">
      {/* Info box */}
      {newCats.length > 0 && (
        <div className="bg-blue-500/10 border border-blue-500/30 border-l-[3px] border-l-violet-500 rounded-lg px-4 py-3 text-[13px] text-blue-300">
          {newCats.length} Sunsky {newCats.length === 1 ? "category" : "categories"} in this batch {newCats.length === 1 ? "has" : "have"} no mapping rule. Assign a WooCommerce category and Attribute Profile for each.
        </div>
      )}

      {/* New categories */}
      {newCats.map((c: any) => {
        const s = sel[c.sunsky_cat] ?? { woo_cat_id: null, profile_id: null, save_as_rule: true };
        return (
          <div key={c.sunsky_cat} className="bg-card border border-border rounded-[10px] p-5">
            <div className="flex items-center gap-3 mb-1 flex-wrap">
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[12px] font-medium bg-amber-500/15 text-amber-400">Unmapped</span>
              <strong className="text-[15px]">{c.sunsky_cat}</strong>
              <span className="text-[12px] text-muted-foreground/60">{c.product_count} product{c.product_count !== 1 ? "s" : ""} in this batch</span>
            </div>
            {c.sample_skus && c.sample_skus.length > 0 && (
              <div className="mb-4 text-[11px] text-muted-foreground/70 font-mono">
                {c.sample_skus.join(", ")}
                {c.product_count > c.sample_skus.length && ` +${c.product_count - c.sample_skus.length} more`}
              </div>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-[12px] font-medium text-foreground/70 mb-1.5">WooCommerce Category</label>
                <select
                  value={s.woo_cat_id ?? ""}
                  onChange={e => setSel(prev => ({ ...prev, [c.sunsky_cat]: { ...s, woo_cat_id: e.target.value ? parseInt(e.target.value) : null } }))}
                  className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                >
                  <option value="">Select category…</option>
                  {wooOptions.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
                {s.woo_cat_id && (
                  <div className="text-[11px] text-violet-400 mt-1">★ AI suggestion: {wooOptions.find(o => o.id === s.woo_cat_id)?.label}</div>
                )}
              </div>
              <div>
                <label className="block text-[12px] font-medium text-foreground/70 mb-1.5">Attribute Profile</label>
                <select
                  value={s.profile_id ?? ""}
                  onChange={e => setSel(prev => ({ ...prev, [c.sunsky_cat]: { ...s, profile_id: e.target.value ? parseInt(e.target.value) : null } }))}
                  className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                >
                  <option value="">— No profile —</option>
                  {profiles.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
            </div>
            <label className="flex items-start gap-2.5 cursor-pointer mt-2">
              <span className={cn(
                "w-4 h-4 min-w-[16px] border-2 rounded flex items-center justify-center mt-0.5 text-[11px] transition-colors",
                s.save_as_rule ? "bg-violet-600 border-violet-600 text-white" : "border-border bg-card"
              )} onClick={() => setSel(prev => ({ ...prev, [c.sunsky_cat]: { ...s, save_as_rule: !s.save_as_rule } }))}>
                {s.save_as_rule && "✓"}
              </span>
              <div>
                <div className="text-[13px] text-foreground/80">Save as permanent rule in Category Mapping</div>
                <div className="text-[12px] text-muted-foreground/60 mt-0.5">Future pipelines with this Sunsky category will not pause again.</div>
              </div>
            </label>
            {newCatForm[c.sunsky_cat]?.open ? (
              <div className="mt-3 flex items-center gap-2 flex-wrap">
                <input
                  autoFocus
                  type="text"
                  placeholder="New category name…"
                  value={newCatForm[c.sunsky_cat]?.name ?? ""}
                  onChange={e => setNewCatForm(f => ({ ...f, [c.sunsky_cat]: { ...f[c.sunsky_cat], name: e.target.value } }))}
                  onKeyDown={e => { if (e.key === "Enter") handleCreateCategory(c.sunsky_cat, pl.store_id); if (e.key === "Escape") setNewCatForm(f => ({ ...f, [c.sunsky_cat]: { open: false, name: "", saving: false } })); }}
                  className="flex-1 min-w-[180px] px-3 py-1.5 border border-border rounded-lg text-[13px] text-foreground bg-background focus:outline-none focus:border-violet-400"
                />
                <button
                  onClick={() => handleCreateCategory(c.sunsky_cat, pl.store_id)}
                  disabled={newCatForm[c.sunsky_cat]?.saving || !newCatForm[c.sunsky_cat]?.name.trim()}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-[12px] font-medium disabled:opacity-50 transition-colors">
                  {newCatForm[c.sunsky_cat]?.saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />} Create
                </button>
                <button
                  onClick={() => setNewCatForm(f => ({ ...f, [c.sunsky_cat]: { open: false, name: "", saving: false } }))}
                  className="px-3 py-1.5 rounded-lg bg-card border border-border text-foreground/60 text-[12px] hover:bg-background transition-colors">
                  Cancel
                </button>
              </div>
            ) : (
              <button onClick={() => openNewCatForm(c.sunsky_cat)} className="inline-flex items-center gap-1 text-violet-400 text-[12px] mt-3 hover:underline">
                <Plus className="w-3 h-3" /> Create new WooCommerce category
              </button>
            )}
          </div>
        );
      })}

      {/* Already mapped */}
      {knownCats.length > 0 && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 border-l-[3px] border-l-emerald-500 rounded-lg px-4 py-3 text-[13px] text-emerald-300">
          <strong>✓ Already mapped — applied automatically</strong><br />
          <span className="text-[12px] mt-0.5 block">
            {knownCats.map(c => `${c.sunsky_cat} (${c.product_count}) → ${c.woo_cats?.[0]?.name ?? "?"}`).join(" · ")}
          </span>
        </div>
      )}

      {/* Confirm button */}
      <div className="flex justify-end">
        <button onClick={handleConfirm} disabled={saving}
          className="inline-flex items-center gap-2 px-7 py-3 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-[13px] font-medium transition-colors disabled:opacity-50">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
          Confirm &amp; Continue →
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Content Review — Substep B (status = content_review)
// ─────────────────────────────────────────────────────────────────────────────

function ContentReviewSection({ pl, onDone }: { pl: Pipeline; onDone: () => void }) {
  const { toast }              = useToast();
  const [data, setData]         = useState<any>(null);
  const [loading, setLoading]   = useState(true);
  const [saving, setSaving]     = useState(false);
  const [tab, setTab]           = useState<"all"|"attention"|"ready">("all");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // Client feedback item #8 (Baselinker reference): "options available
  // for manual editing: quantity, sales and regular prices, woo sku and
  // to be able to edit all details." drafts holds UNSAVED per-product
  // field edits; getField reads the draft if present, else the loaded
  // value -- so the UI shows what the operator typed even before saving.
  const [drafts, setDrafts] = useState<Record<number, Record<string, any>>>({});
  const [savingProduct, setSavingProduct] = useState<number | null>(null);

  const getField = (p: any, field: string) => drafts[p.id]?.[field] ?? p[field] ?? "";
  const setDraftField = (pid: number, field: string, value: any) =>
    setDrafts(prev => ({ ...prev, [pid]: { ...prev[pid], [field]: value } }));
  const hasDraft = (pid: number) => !!drafts[pid] && Object.keys(drafts[pid]).length > 0;

  const handleSaveProduct = async (pid: number) => {
    const changes = drafts[pid];
    if (!changes) return;
    setSavingProduct(pid);
    try {
      // stock_quantity must be a number (or null), not the raw string
      // the input naturally produces.
      const body: any = { ...changes };
      if ("stock_quantity" in body) {
        body.stock_quantity = body.stock_quantity === "" ? null : Number(body.stock_quantity);
      }
      const r = await fetch(`/api/products/${pid}/fields`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      // Merge saved values into local product list so the UI reflects
      // the save without needing a full re-fetch of every product.
      setData((prev: any) => ({
        ...prev,
        products: (prev?.products ?? []).map((p: any) => p.id === pid ? { ...p, ...changes } : p),
      }));
      setDrafts(prev => { const next = { ...prev }; delete next[pid]; return next; });
      toast({ title: "Saved" });
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSavingProduct(null);
    }
  };
  const [goingBack, setGoingBack] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [storeCats, setStoreCats] = useState<{ id: number; name: string }[]>([]);
  const [savingCategory, setSavingCategory] = useState<number | null>(null);
  const [categoryDraft, setCategoryDraft] = useState<Record<number, string>>({});

  useEffect(() => {
    fetch(`/api/pipelines/${pl.id}/content-data`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(setData)
      .catch(() => toast({ title: "Failed to load content data", variant: "destructive" }))
      .finally(() => setLoading(false));
    if (pl.store_id) {
      fetch(`/api/stores/${pl.store_id}/categories`)
        .then(r => r.ok ? r.json() : [])
        .then(d => setStoreCats((Array.isArray(d) ? d : []).map((c: any) => ({ id: c.wooId ?? c.woo_id ?? c.id, name: c.name }))))
        .catch(() => {});
    }
  }, [pl.id]);

  const handleSaveCategory = async (pid: number) => {
    // Client feedback item #8: "to be able to edit all details." Backend
    // mechanism (cat_source="manual") already existed and is genuinely
    // respected at real Upload/Sync time -- this was just never exposed
    // in Content Review's own UI before.
    const selectedId = categoryDraft[pid];
    if (!selectedId) return;
    const cat = storeCats.find(c => String(c.id) === selectedId);
    if (!cat) return;
    setSavingCategory(pid);
    try {
      const r = await fetch(`/api/products/${pid}/categories`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ woo_cats: [{ id: cat.id, name: cat.name }], primary_woo_cat_id: cat.id }),
      });
      if (!r.ok) throw new Error(await r.text());
      setData((prev: any) => ({
        ...prev,
        products: (prev?.products ?? []).map((p: any) =>
          p.id === pid ? { ...p, category_name: cat.name, category_mapped: true, cat_source: "manual" } : p
        ),
      }));
      setCategoryDraft(prev => { const next = { ...prev }; delete next[pid]; return next; });
      toast({ title: "Category updated" });
    } catch (e: any) {
      toast({ title: "Failed to update category", description: e.message, variant: "destructive" });
    } finally {
      setSavingCategory(null);
    }
  };

  const allProducts: any[]    = data?.products ?? [];
  const needsAttention        = allProducts.filter(p => p.needs_attention && !excluded.has(p.id));
  const ready                 = allProducts.filter(p => !p.needs_attention && !excluded.has(p.id));

  const [imageActionLoading, setImageActionLoading] = useState<number | null>(null);

  const handleDeleteImage = async (pid: number, imageId: number) => {
    setImageActionLoading(imageId);
    try {
      const r = await fetch(`/api/products/${pid}/images/${imageId}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      setData((prev: any) => ({
        ...prev,
        products: (prev?.products ?? []).map((p: any) => {
          if (p.id !== pid) return p;
          const nextImages = (p.images ?? []).filter((img: any) => img.id !== imageId);
          return { ...p, images: nextImages, image_count: nextImages.length };
        }),
      }));
      toast({ title: "Image excluded" });
    } catch (e: any) {
      toast({ title: "Failed to exclude image", description: e.message, variant: "destructive" });
    } finally {
      setImageActionLoading(null);
    }
  };

  const handleMoveImage = async (pid: number, idx: number, direction: -1 | 1) => {
    const product = allProducts.find((p: any) => p.id === pid);
    const images = product?.images ?? [];
    const targetIdx = idx + direction;
    if (targetIdx < 0 || targetIdx >= images.length) return;

    const reordered = [...images];
    [reordered[idx], reordered[targetIdx]] = [reordered[targetIdx], reordered[idx]];
    const orderedIds = reordered.map((img: any) => img.id);

    setImageActionLoading(reordered[idx].id);
    // Optimistic update -- reflect the new order immediately, revert on failure.
    setData((prev: any) => ({
      ...prev,
      products: (prev?.products ?? []).map((p: any) => p.id === pid ? { ...p, images: reordered } : p),
    }));
    try {
      const r = await fetch(`/api/products/${pid}/images/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_ids: orderedIds }),
      });
      if (!r.ok) throw new Error(await r.text());
    } catch (e: any) {
      toast({ title: "Failed to reorder images", description: e.message, variant: "destructive" });
      // Revert on failure
      setData((prev: any) => ({
        ...prev,
        products: (prev?.products ?? []).map((p: any) => p.id === pid ? { ...p, images } : p),
      }));
    } finally {
      setImageActionLoading(null);
    }
  };

  const displayed = useMemo(() => {
    const active = allProducts.filter(p => !excluded.has(p.id));
    if (tab === "attention") return active.filter(p => p.needs_attention);
    if (tab === "ready")     return active.filter(p => !p.needs_attention);
    return active;
  }, [allProducts, tab, excluded]);

  const handleUploadAll = async () => {
    setSaving(true);
    try {
      const r = await fetch(`/api/pipelines/${pl.id}/content-confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ excluded_product_ids: Array.from(excluded) }),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Upload started", description: "Products are being uploaded to WooCommerce…" });
      onDone();
    } catch (e: any) {
      toast({ title: "Failed to start upload", description: e.message, variant: "destructive" });
    } finally { setSaving(false); }
  };

  const handleAssignCategory = async () => {
    // Client feedback item #10 ("major issue"): "Can't go back on
    // previous steps if want to change something." Goes back to Category
    // Review without losing any already-generated content -- it's all
    // saved in the DB independent of which screen is currently showing.
    if (!confirm("Go back to Category Review? Your generated content won't be lost — you'll return here after re-confirming categories.")) return;
    setGoingBack(true);
    try {
      const r = await fetch(`/api/pipelines/${pl.id}/back-to-category-review`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Back to Category Review" });
      onDone();
    } catch (e: any) {
      toast({ title: "Failed to go back", description: e.message, variant: "destructive" });
    } finally { setGoingBack(false); }
  };

  const handleRegenerateContent = async () => {
    if (!confirm("Re-generate content for this pipeline's products? This runs content generation again and will overwrite the current generated fields.")) return;
    setRegenerating(true);
    try {
      const r = await fetch(`/api/pipelines/${pl.id}/regenerate-content`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Re-generating content", description: "This may take a moment — the page will update automatically." });
      onDone();
    } catch (e: any) {
      toast({ title: "Failed to start re-generation", description: e.message, variant: "destructive" });
      setRegenerating(false);
    }
  };

  const handleExcludeSelected = () => {
    // Reuses the already-working single-product "Exclude from upload"
    // mechanism (patch 31) -- excluded_product_ids flows correctly to
    // content-confirm and _run_upload already. Bulk exclude is just
    // "add every currently-selected ID to that same set."
    if (selected.size === 0) {
      toast({ title: "Nothing selected", description: "Check the products you want to exclude first." });
      return;
    }
    setExcluded(prev => {
      const s = new Set(prev);
      selected.forEach(id => s.add(id));
      return s;
    });
    toast({ title: `${selected.size} product${selected.size !== 1 ? "s" : ""} excluded from upload` });
    setSelected(new Set());
  };

  if (loading) return <div className="flex items-center gap-2 py-8 text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  const readyCount = ready.length;

  return (
    <div className="space-y-4">
      {/* Substep B card */}
      <div className="bg-card border border-emerald-500/30 border-l-[3px] border-l-emerald-500 rounded-[10px] p-5">
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <span className="w-[22px] h-[22px] rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-[12px] font-bold flex-shrink-0">2</span>
          <div className="text-[13px] font-semibold text-emerald-400">Substep B — Review generated content</div>
          <div className="ml-auto">
            <div className="flex gap-1 bg-muted/50 rounded-lg p-1">
              {(["all","attention","ready"] as const).map(t => (
                <button key={t} onClick={() => setTab(t)}
                  className={cn("px-3 py-1 rounded-md text-[13px] font-medium transition-colors",
                    tab === t ? "bg-muted text-violet-400 shadow-sm" : "text-muted-foreground hover:text-foreground"
                  )}>
                  {t === "all" ? `All (${allProducts.length})` : t === "attention" ? `Needs attention (${needsAttention.length})` : `Ready (${ready.length})`}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Product rows */}
        <div className="flex items-center gap-2 mb-2 px-1">
          <input
            type="checkbox"
            checked={displayed.length > 0 && displayed.every((p: any) => selected.has(p.id))}
            onChange={() => setSelected(prev => {
              const allSelected = displayed.length > 0 && displayed.every((p: any) => prev.has(p.id));
              if (allSelected) {
                const s = new Set(prev);
                displayed.forEach((p: any) => s.delete(p.id));
                return s;
              }
              const s = new Set(prev);
              displayed.forEach((p: any) => s.add(p.id));
              return s;
            })}
            className="w-3.5 h-3.5 rounded shrink-0 cursor-pointer accent-violet-500"
          />
          <span className="text-[12px] text-muted-foreground">
            {selected.size > 0 ? `${selected.size} selected` : "Select all visible"}
          </span>
        </div>
        <div className="space-y-2 mb-4">
          {displayed.slice(0, 50).map((p: any) => {
            const isExp = expanded.has(p.id);
            return (
              <div key={p.id} className="border border-border rounded-lg overflow-hidden">
                <div
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer bg-card hover:bg-card/50"
                  onClick={() => setExpanded(prev => { const s = new Set(prev); s.has(p.id) ? s.delete(p.id) : s.add(p.id); return s; })}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(p.id)}
                    onClick={e => e.stopPropagation()}
                    onChange={() => setSelected(prev => { const s = new Set(prev); s.has(p.id) ? s.delete(p.id) : s.add(p.id); return s; })}
                    className="w-3.5 h-3.5 rounded shrink-0 cursor-pointer accent-violet-500"
                  />
                  {p.needs_attention
                    ? <span className="inline-flex px-2 py-0.5 rounded-full text-[12px] font-medium bg-amber-500/15 text-amber-400 flex-shrink-0">Needs attention</span>
                    : <span className="inline-flex px-2 py-0.5 rounded-full text-[12px] font-medium bg-emerald-500/15 text-emerald-400 flex-shrink-0">Ready</span>
                  }
                  <strong className="text-[13px]">{p.name}</strong>
                  <span className="text-[12px] text-muted-foreground">{p.sku}{p.price ? ` · $${p.price}` : ""}</span>
                  <span className="ml-auto text-muted-foreground/60 text-[12px]">{isExp ? "▾" : "›"}</span>
                </div>
                {isExp && (
                  <div className="px-4 pb-4 bg-card/50 border-t border-border">
                    <div className="grid grid-cols-1 gap-3 mt-3">
                      <div>
                        <label className="block text-[12px] font-medium text-foreground/70 mb-1">Product Title</label>
                        <input
                          value={getField(p, "name")}
                          onChange={e => setDraftField(p.id, "name", e.target.value)}
                          className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                        />
                      </div>

                      {/* Qty / Price row -- Baselinker reference: "quantity,
                          sales and regular prices, woo sku" all editable
                          inline, not buried behind a separate screen. */}
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Woo SKU</label>
                          <input
                            value={getField(p, "site_sku")}
                            onChange={e => setDraftField(p.id, "site_sku", e.target.value)}
                            placeholder={p.sku}
                            className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400 font-mono"
                          />
                        </div>
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Stock Qty</label>
                          <input
                            type="number"
                            value={getField(p, "stock_quantity")}
                            onChange={e => setDraftField(p.id, "stock_quantity", e.target.value)}
                            className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                          />
                        </div>
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Regular Price</label>
                          <input
                            value={getField(p, "price")}
                            onChange={e => setDraftField(p.id, "price", e.target.value)}
                            placeholder="0.00"
                            className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                          />
                        </div>
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Sale Price</label>
                          <input
                            value={getField(p, "sale_price")}
                            onChange={e => setDraftField(p.id, "sale_price", e.target.value)}
                            placeholder="Optional"
                            className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                          />
                        </div>
                      </div>

                      <div>
                        <label className="block text-[12px] font-medium text-foreground/70 mb-1">Description</label>
                        <textarea
                          value={getField(p, "description")}
                          onChange={e => setDraftField(p.id, "description", e.target.value)}
                          rows={3}
                          className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400 resize-y"
                        />
                      </div>

                      {/* SEO fields -- client feedback: Content Review only
                          ever showed Title/Description/Images/Category/
                          Attributes; Slug, Meta Title, Meta Description,
                          and Focus Keyword existed on every product the
                          whole time but were never visible or editable
                          here at all. */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Slug</label>
                          <input
                            value={getField(p, "slug")}
                            onChange={e => setDraftField(p.id, "slug", e.target.value)}
                            className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400 font-mono"
                          />
                        </div>
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Focus Keyword</label>
                          <input
                            value={getField(p, "focus_keyword")}
                            onChange={e => setDraftField(p.id, "focus_keyword", e.target.value)}
                            className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                          />
                        </div>
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Meta Title</label>
                          <input
                            value={getField(p, "meta_title")}
                            onChange={e => setDraftField(p.id, "meta_title", e.target.value)}
                            className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                          />
                        </div>
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Meta Description</label>
                          <input
                            value={getField(p, "meta_description")}
                            onChange={e => setDraftField(p.id, "meta_description", e.target.value)}
                            className="w-full px-3 py-2 border border-border rounded-lg text-[13px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                          />
                        </div>
                      </div>

                      {hasDraft(p.id) && (
                        <div className="flex items-center gap-2 sticky bottom-0 bg-card/95 backdrop-blur-sm py-2 -mx-4 px-4 border-t border-violet-500/30">
                          <button
                            onClick={() => handleSaveProduct(p.id)}
                            disabled={savingProduct === p.id}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-500 hover:bg-violet-600 text-white text-[12px] font-medium disabled:opacity-50"
                          >
                            {savingProduct === p.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                            Save changes
                          </button>
                          <button
                            onClick={() => setDrafts(prev => { const next = { ...prev }; delete next[p.id]; return next; })}
                            className="text-[12px] text-muted-foreground hover:text-foreground px-2"
                          >
                            Discard
                          </button>
                        </div>
                      )}

                      {p.image_count > 0 && (
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Images</label>
                          <div className="flex gap-3 flex-wrap">
                            {p.images && p.images.length > 0 ? (
                              p.images.map((img: any, idx: number) => (
                                <div key={img.id} className="flex flex-col items-center gap-1">
                                  <a
                                    href={img.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    title={idx === 0 ? "Main image — open full size" : "Open full size"}
                                    className={cn(
                                      "w-11 h-11 rounded-lg overflow-hidden border block hover:border-violet-400 transition-colors bg-muted/50",
                                      idx === 0 ? "border-violet-400" : "border-border"
                                    )}
                                  >
                                    <img
                                      src={img.url}
                                      alt={`${p.name} image ${idx + 1}`}
                                      className="w-full h-full object-cover"
                                      onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }}
                                    />
                                  </a>
                                  <div className="flex items-center gap-0.5">
                                    <button
                                      onClick={() => handleMoveImage(p.id, idx, -1)}
                                      disabled={idx === 0 || imageActionLoading === img.id}
                                      title="Move earlier"
                                      className="w-4 h-4 flex items-center justify-center text-muted-foreground hover:text-foreground disabled:opacity-20 disabled:cursor-default"
                                    >
                                      <ChevronRight className="w-3 h-3 rotate-180" />
                                    </button>
                                    <button
                                      onClick={() => handleDeleteImage(p.id, img.id)}
                                      disabled={imageActionLoading === img.id}
                                      title="Exclude this image"
                                      className="w-4 h-4 flex items-center justify-center text-muted-foreground hover:text-red-400 disabled:opacity-30"
                                    >
                                      {imageActionLoading === img.id ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <XIcon className="w-3 h-3" />}
                                    </button>
                                    <button
                                      onClick={() => handleMoveImage(p.id, idx, 1)}
                                      disabled={idx === p.images.length - 1 || imageActionLoading === img.id}
                                      title="Move later"
                                      className="w-4 h-4 flex items-center justify-center text-muted-foreground hover:text-foreground disabled:opacity-20 disabled:cursor-default"
                                    >
                                      <ChevronRight className="w-3 h-3" />
                                    </button>
                                  </div>
                                </div>
                              ))
                            ) : (
                              // image_count > 0 but no URLs resolved (e.g. no
                              // server_base_url configured and no original_url
                              // saved) -- shown honestly as unavailable rather
                              // than as fake "img1/img2" placeholders that
                              // implied images existed and were clickable when
                              // nothing was actually there.
                              Array.from({ length: p.image_count }).map((_, idx) => (
                                <div key={idx} title="Image URL unavailable" className="w-11 h-11 bg-muted/50 border border-border rounded-lg flex items-center justify-center text-[9px] text-muted-foreground/50">
                                  N/A
                                </div>
                              ))
                            )}
                          </div>
                        </div>
                      )}

                      {/* Category — client feedback item #8: "to be able
                          to edit all details." Backend override mechanism
                          (cat_source="manual") already existed and is
                          genuinely respected at real Upload/Sync time --
                          this was just never exposed in this screen. */}
                      <div>
                        <label className="block text-[12px] font-medium text-foreground/70 mb-1">Category</label>
                        <div className="flex items-center gap-2 mb-2">
                          {p.category_name ? (
                            <>
                              <span className="text-sm">{p.category_name}</span>
                              {p.cat_source === "manual" ? (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-400 border border-violet-500/20" title="Manually overridden — this exact category will be used at upload, regardless of the batch's Sunsky category mapping">manual</span>
                              ) : p.category_mapped ? (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">mapped</span>
                              ) : (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20" title="Sunsky category name shown — no saved WooCommerce mapping found yet">unmapped</span>
                              )}
                            </>
                          ) : (
                            <span className="text-sm text-muted-foreground italic">No category resolved</span>
                          )}
                        </div>
                        {storeCats.length > 0 && (
                          <div className="flex items-center gap-2">
                            <select
                              value={categoryDraft[p.id] ?? ""}
                              onChange={e => setCategoryDraft(prev => ({ ...prev, [p.id]: e.target.value }))}
                              className="flex-1 px-3 py-1.5 border border-border rounded-lg text-[12px] text-foreground bg-card focus:outline-none focus:border-violet-400"
                            >
                              <option value="">Override category…</option>
                              {storeCats.map(c => (
                                <option key={c.id} value={c.id}>{c.name}</option>
                              ))}
                            </select>
                            <button
                              onClick={() => handleSaveCategory(p.id)}
                              disabled={!categoryDraft[p.id] || savingCategory === p.id}
                              className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-violet-500 hover:bg-violet-600 text-white disabled:opacity-50 flex items-center gap-1"
                            >
                              {savingCategory === p.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                              Set
                            </button>
                          </div>
                        )}
                      </div>

                      {/* Attributes — same data checked via SQL all session,
                          now visible directly here instead. */}
                      {p.attributes && p.attributes.length > 0 && (
                        <div>
                          <label className="block text-[12px] font-medium text-foreground/70 mb-1">Attributes</label>
                          <div className="flex flex-wrap gap-1.5">
                            {p.attributes.map((a: any, idx: number) => (
                              <span
                                key={idx}
                                title={`source: ${a.source}`}
                                className={cn(
                                  "text-[11px] px-2 py-1 rounded-lg border",
                                  a.flagged || !a.raw_value
                                    ? "bg-red-500/5 border-red-500/20 text-red-400/90"
                                    : "bg-emerald-500/5 border-emerald-500/20 text-emerald-400/90"
                                )}
                              >
                                {a.attribute}: {a.raw_value || "not found"}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="mt-3 pt-3 border-t border-border flex gap-2">
                      <button onClick={() => setExcluded(prev => { const s = new Set(prev); s.add(p.id); return s; })}
                        className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-card border border-border text-foreground/70 hover:bg-background">
                        Exclude from upload
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
          {displayed.length > 50 && <div className="text-[12px] text-muted-foreground italic px-2">Showing 50 of {displayed.length} products</div>}
          {displayed.length === 0 && <div className="text-[13px] text-muted-foreground italic py-4 text-center">No products in this view.</div>}
        </div>

        {/* Footer actions */}
        <div className="flex items-center justify-between pt-3 border-t border-border flex-wrap gap-3">
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={handleExcludeSelected}
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-card border border-border text-foreground/70 hover:bg-background flex items-center gap-1.5"
            >
              Exclude selected{selected.size > 0 ? ` (${selected.size})` : ""}
            </button>
            <button
              onClick={handleRegenerateContent}
              disabled={regenerating}
              title="Re-run content generation for this pipeline's products"
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-card border border-border text-foreground/70 hover:bg-background disabled:opacity-50 flex items-center gap-1.5"
            >
              {regenerating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
              Re-generate content
            </button>
            <button
              onClick={handleAssignCategory}
              disabled={goingBack}
              title="Go back to Category Review to fix a category assignment"
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-card border border-border text-foreground/70 hover:bg-background disabled:opacity-50 flex items-center gap-1.5"
            >
              {goingBack ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
              Assign category
            </button>
          </div>
          <button onClick={handleUploadAll} disabled={saving}
            className="inline-flex items-center gap-2 px-5 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-[13px] font-medium transition-colors disabled:opacity-50">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
            Upload All Ready ({readyCount}) →
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Completed section (with failed products table)
// ─────────────────────────────────────────────────────────────────────────────

function CompletedSection({ pl, plId }: { pl: Pipeline; plId: number }) {
  const stats    = pl.stats_json ?? {};
  const uploaded = stats.uploaded ?? stats.ok ?? 0;
  const failed   = stats.failed ?? 0;
  const excluded = stats.excluded ?? stats.skipped ?? 0;

  const [failedProducts, setFailedProducts] = useState<any[]>([]);

  useEffect(() => {
    if (failed > 0) {
      fetch(`/api/products?job_id=${pl.fetch_job_id}&status=failed&limit=50`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.products) setFailedProducts(d.products); })
        .catch(() => {});
    }
  }, [failed, pl.fetch_job_id]);

  return (
    <div className="space-y-5">
      {/* 3 stat cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-card border border-border rounded-[10px] px-5 py-4">
          <div className="text-[12px] text-muted-foreground mb-1.5">Uploaded successfully</div>
          <div className="text-[28px] font-bold text-emerald-400 leading-none tracking-[-1px]">{uploaded}</div>
        </div>
        <div className="bg-card border border-border rounded-[10px] px-5 py-4">
          <div className="text-[12px] text-muted-foreground mb-1.5">Failed</div>
          <div className="text-[28px] font-bold leading-none tracking-[-1px]" style={{ color: failed > 0 ? "rgb(248 113 113)" : "currentColor" }}>{failed}</div>
        </div>
        <div className="bg-card border border-border rounded-[10px] px-5 py-4">
          <div className="text-[12px] text-muted-foreground mb-1.5">Excluded</div>
          <div className="text-[28px] font-bold text-muted-foreground/60 leading-none tracking-[-1px]">{excluded}</div>
        </div>
      </div>

      {/* Failed Products table */}
      {failed > 0 && (
        <div className="bg-card border border-red-500/30 rounded-[10px] overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-red-500/20">
            <div className="text-[11px] font-semibold text-red-400 uppercase tracking-[0.6px]">Failed Products ({failed})</div>
            <button className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-card border border-border text-foreground/70 hover:bg-background">↺ Retry all failed</button>
          </div>
          {failedProducts.length > 0 ? (
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="bg-red-500/10">
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.5px]">SKU</th>
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.5px]">Product</th>
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.5px]">Error</th>
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.5px]">Action</th>
                </tr>
              </thead>
              <tbody>
                {failedProducts.map((p: any) => (
                  <tr key={p.id} className="border-t border-red-500/20 hover:bg-red-500/10">
                    <td className="px-4 py-3 text-[12px] text-muted-foreground font-mono">{p.sku}</td>
                    <td className="px-4 py-3 font-medium">{p.name}</td>
                    <td className="px-4 py-3 text-[12px] text-red-400">{p.error_message ?? "Unknown error"}</td>
                    <td className="px-4 py-3"><button className="px-2.5 py-1 rounded-md text-[11px] bg-card border border-border text-foreground/70 hover:bg-background cursor-pointer">Retry</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="px-5 py-4 text-[13px] text-muted-foreground italic">Check the log below for details on failed products.</div>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center gap-3 justify-end">
        <Link href="/products">
          <button className="px-4 py-2 rounded-lg bg-card border border-border text-foreground/70 text-[13px] font-medium hover:bg-background transition-colors">
            View Products
          </button>
        </Link>
        <Link href="/pipeline">
          <button className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-[13px] font-medium transition-colors">
            <Zap className="w-3.5 h-3.5" /> + New Pipeline
          </button>
        </Link>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Failed / Cancelled section
// ─────────────────────────────────────────────────────────────────────────────

function FailedSection({ pl, onAction }: { pl: Pipeline; onAction: (a: string) => void }) {
  return (
    <div className="space-y-4">
      <div className="bg-red-500/10 border border-red-500/30 border-l-[3px] border-l-red-500 rounded-lg px-4 py-3">
        <div className="flex items-start gap-2">
          <XCircle className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
          <div>
            <p className="font-semibold text-red-400 text-[13px]">Pipeline {pl.status === "cancelled" ? "Cancelled" : "Failed"}</p>
            {pl.error_message && <p className="text-[12px] text-red-400 mt-1 font-mono">{pl.error_message}</p>}
          </div>
        </div>
      </div>
      <div className="flex gap-3">
        <button onClick={() => onAction("continue")}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/30 text-[13px] font-medium transition-colors">
          <Play className="w-3.5 h-3.5 fill-current" /> Continue from last step
        </button>
        <button onClick={() => onAction("retry")}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-card border border-border text-foreground/70 text-[13px] font-medium hover:bg-background transition-colors">
          <RotateCcw className="w-3.5 h-3.5" /> Retry from scratch
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Page title / badge helpers
// ─────────────────────────────────────────────────────────────────────────────

function pageTitle(status: string): string {
  if (status === "running")        return "Pipeline Running";
  if (status === "enrich_review")  return "Review Before Upload";
  if (status === "review")         return "Category Assignment Required";
  if (status === "content_review") return "Review Before Upload";
  if (status === "completed")      return "Pipeline Completed";
  if (status === "failed")         return "Pipeline Failed";
  if (status === "cancelled")      return "Pipeline Cancelled";
  if (status === "queued")         return "Pipeline Queued";
  return "Pipeline";
}

function StatusBadge({ status }: { status: string }) {
  if (status === "running")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-violet-500/20 text-violet-300"><span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse inline-block" /> Running</span>;
  if (["enrich_review","review","content_review"].includes(status))
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-amber-500/20 text-amber-300"><span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse inline-block" /> Waiting for input</span>;
  if (status === "completed")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-emerald-500/20 text-emerald-300"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" /> Completed</span>;
  if (status === "failed")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-red-500/20 text-red-400"><span className="w-1.5 h-1.5 rounded-full bg-red-400 inline-block" /> Failed</span>;
  if (status === "cancelled")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-muted text-muted-foreground">Cancelled</span>;
  if (status === "queued")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-muted text-muted-foreground"><span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 inline-block" /> Queued</span>;
  return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-muted text-muted-foreground">{status}</span>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Demo mode — build a mock Pipeline for each prototype state
// ─────────────────────────────────────────────────────────────────────────────

function buildDemoPipeline(state: string): Pipeline {
  const base: Pipeline = {
    id: 0,
    pl_id: "PL-069",
    store_id: 1,
    fetch_job_id: 1,
    status: state || "running",
    current_step: state === "running" ? "enrich" : null,
    config: {},
    stats_json: state === "completed" ? { uploaded: 42, failed: 3, excluded: 2 } : null,
    error_message: null,
    created_at: new Date(Date.now() - 3_600_000).toISOString(),
    updated_at: new Date().toISOString(),
    step_jobs: state === "running" ? [{
      id: 1, type: "enrich", status: "running",
      total_items: 47, processed_items: 23, failed_items: 0,
      progress_percent: 49, error_message: null,
      started_at: new Date(Date.now() - 120_000).toISOString(),
      completed_at: null,
    }] : [],
  };
  return base;
}

export default function PipelineDetail() {
  const [, params] = useRoute("/pipelines/:id");
  const [, navigate] = useLocation();
  const search = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : new URLSearchParams();
  const { toast } = useToast();
  const { data: stores } = useStores();

  const isDemo = params?.id === "demo";
  const demoState = search.get("state") ?? "running";
  const plId = isDemo ? 0 : parseInt(params?.id ?? "0");

  const [pl, setPl]           = useState<Pipeline | null>(isDemo ? buildDemoPipeline(demoState) : null);
  const [loading, setLoading] = useState(!isDemo);
  const [error, setError]     = useState<string | null>(null);
  const pollRef               = useRef<ReturnType<typeof setInterval> | null>(null);
  const [logOpen, setLogOpen] = useState(true);
  const [deleting, setDeleting] = useState(false);

  const storeMap = Object.fromEntries((stores ?? []).map(s => [s.id, s.name]));

  const fetchPipeline = useCallback(async () => {
    if (isDemo) { setPl(buildDemoPipeline(demoState)); return; }
    try {
      const r = await fetch(`/api/pipelines/${plId}`);
      if (!r.ok) throw new Error(`Pipeline not found (${r.status})`);
      setPl(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [plId, isDemo, demoState]);

  const isLive   = pl ? ["running","queued"].includes(pl.status) : false;
  const isReview = pl ? ["review","enrich_review","content_review"].includes(pl.status) : false;

  useEffect(() => { fetchPipeline(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; }, [fetchPipeline]);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (isLive || isReview) pollRef.current = setInterval(fetchPipeline, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [isLive, isReview, fetchPipeline]);

  const handleAction = async (action: string) => {
    try {
      const r = await fetch(`/api/pipelines/${plId}/${action}`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      if (action === "retry") { toast({ title: "Retry started", description: `New pipeline ${d.pl_id} created` }); navigate(`/pipelines/${d.id}`); }
      else { toast({ title: "Action completed" }); fetchPipeline(); }
    } catch (e: any) {
      toast({ title: "Action failed", description: e.message, variant: "destructive" });
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete ${pl?.pl_id ?? "this pipeline"}? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      const r = await fetch(`/api/pipelines/${plId}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Pipeline deleted" });
      navigate("/pipelines");
    } catch (e: any) {
      toast({ title: "Delete failed", description: e.message, variant: "destructive" });
      setDeleting(false);
    }
  };

  if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 animate-spin text-violet-500" /></div>;

  if (error || !pl) return (
    <div className="space-y-4 p-6">
      <Link href="/pipelines"><button className="flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="w-4 h-4" /> All Runs</button></Link>
      <div className="bg-red-500/10 border border-red-500/30 rounded-[10px] p-6 text-red-400">{error ?? "Pipeline not found"}</div>
    </div>
  );

  const storeName  = storeMap[pl.store_id];
  const storeColor = getStoreColor(pl.store_id);

  return (
    <div className="space-y-5 max-w-4xl">
      {/* Breadcrumb */}
      <Link href="/pipelines">
        <button className="flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> All Runs
        </button>
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="text-[12px] text-muted-foreground mb-1">{pl.pl_id} · {storeName ?? `Store #${pl.store_id}`}{pl.status === "completed" && pl.updated_at ? ` · ${format(new Date(pl.updated_at), "MMM d, yyyy — hh:mm aa")}` : ""}</div>
          <h1 className="text-[22px] font-bold text-foreground tracking-[-0.3px]">{pageTitle(pl.status)}</h1>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={pl.status} />
          {(isLive || isReview) && (
            <button onClick={() => handleAction("cancel")}
              className="px-3 py-1.5 rounded-lg bg-card border border-border text-red-400 text-[12px] font-medium hover:bg-red-500/10 transition-colors flex items-center gap-1.5">
              <Square className="w-3 h-3 fill-current" /> Cancel
            </button>
          )}
          {!isDemo && !isLive && !isReview && (
            <button onClick={handleDelete} disabled={deleting}
              className="px-3 py-1.5 rounded-lg bg-card border border-border text-red-400 text-[12px] font-medium hover:bg-red-500/10 transition-colors flex items-center gap-1.5 disabled:opacity-50">
              {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />} Delete
            </button>
          )}
          <button onClick={fetchPipeline}
            className="px-3 py-1.5 rounded-lg bg-card border border-border text-foreground/70 text-[12px] font-medium hover:bg-background transition-colors flex items-center gap-1.5">
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
        </div>
      </div>

      {/* Stage Trail */}
      <div className="bg-card border border-border rounded-[10px] p-4">
        <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.6px] mb-3">Pipeline Stages</div>
        <StageTrail pl={pl} />
      </div>

      {/* State-specific content */}
      {pl.status === "running" && <RunningSection pl={pl} />}

      {pl.status === "queued" && (
        <div className="bg-card border border-border rounded-[10px] p-5 flex items-start gap-3">
          <Clock className="w-5 h-5 text-muted-foreground/60 mt-0.5 flex-shrink-0" />
          <div>
            <p className="font-semibold text-foreground">Queued</p>
            <p className="text-[13px] text-muted-foreground mt-1">This pipeline is waiting for another pipeline on the same store to complete. It will start automatically.</p>
          </div>
        </div>
      )}

      {pl.status === "enrich_review" && <EnrichReviewSection pl={pl} onDone={fetchPipeline} />}

      {pl.status === "review" && <CategoryReviewSection pl={pl} onDone={fetchPipeline} />}

      {pl.status === "content_review" && <ContentReviewSection pl={pl} onDone={fetchPipeline} />}

      {pl.status === "completed" && <CompletedSection pl={pl} plId={plId} />}

      {(pl.status === "failed" || pl.status === "cancelled") && <FailedSection pl={pl} onAction={handleAction} />}

      {/* Log Panel */}
      <div className="bg-card border border-border rounded-[10px] overflow-hidden">
        <button onClick={() => setLogOpen(x => !x)}
          className="w-full flex items-center gap-2 px-4 py-3 border-b border-border hover:bg-background text-left">
          <Terminal className="w-4 h-4 text-muted-foreground/60" />
          <span className="text-[13px] font-medium text-foreground/70">Pipeline Log</span>
          {isLive && <span className="flex items-center gap-1 text-[12px] text-violet-400 ml-2"><span className="w-1.5 h-1.5 bg-violet-400 rounded-full animate-pulse inline-block" /> Live</span>}
          <span className="ml-auto text-muted-foreground/60 text-[12px]">{logOpen ? "▲" : "▼"}</span>
        </button>
        {logOpen && <LogPanel plId={plId} isLive={isLive} />}
      </div>
    </div>
  );
}
