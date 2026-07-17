import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useRoute, Link, useLocation } from "wouter";
import {
  ArrowLeft, CheckCircle2, XCircle, Loader2, Clock, Play,
  RotateCcw, Square, RefreshCw, AlertTriangle, Check, X as XIcon,
  Upload, Eye, Terminal, Zap, ChevronDown, ChevronRight,
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
              isDone   && "bg-emerald-50 border-emerald-200 text-emerald-700",
              isActive && isPaused  && "bg-amber-50 border-amber-300 text-amber-800 font-semibold",
              isActive && !isPaused && "bg-violet-50 border-violet-300 text-violet-800 font-semibold",
              isFail   && "bg-red-50 border-red-200 text-red-700",
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
  ok:    "text-emerald-600",
  warn:  "text-amber-600",
  error: "text-red-600",
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
    <div className="max-h-[150px] overflow-y-auto bg-[#F8FAFC] font-mono text-[12px] p-3 space-y-0.5">
      {logs.map(log => (
        <div key={log.id} className="flex gap-2">
          <span className="text-[#94A3B8] shrink-0">{log.created_at ? format(new Date(log.created_at), "HH:mm:ss") : ""}</span>
          {log.level === "ok"   && <span className="text-emerald-600 shrink-0">✓</span>}
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
  process: "Downloading images, resizing, watermarking, converting to WebP.",
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
        <div className="bg-white border border-[#E2E8F0] rounded-[10px] p-5">
          <div className="text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.6px] mb-3">Progress</div>
          <div className="text-[30px] font-bold text-[#0F172A] leading-none tracking-[-1px] mb-2">
            {total > 0 ? done : "—"}{" "}
            <span className="text-[15px] font-normal text-[#94A3B8]">
              {total > 0 ? `/ ${total} products` : "products"}
            </span>
          </div>
          <div className="bg-[#F1F5F9] rounded-full h-1.5 overflow-hidden mb-2">
            <div className="h-full bg-violet-500 rounded-full transition-all duration-500" style={{ width: `${Math.min(100, pct)}%` }} />
          </div>
          <div className="text-[12px] text-[#64748B]">
            {pct}% · {stageLabel}{total > 0 ? "" : " · starting…"}
          </div>
        </div>

        {/* CURRENT STAGE */}
        <div className="bg-white border border-[#E2E8F0] rounded-[10px] p-5">
          <div className="text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.6px] mb-3">Current Stage</div>
          <div className="text-[14px] font-semibold text-violet-700 mb-2">{stageLabel}</div>
          <div className="text-[13px] text-[#64748B] whitespace-pre-line leading-[1.7]">
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
  const needsReview = allProducts.filter((p: any) => p.attrs?.some((a: any) => !a.value || a.confidence === "low")).length;
  const ready       = allProducts.length - needsReview;

  const displayed = useMemo(() => {
    if (tab === "review") return allProducts.filter((p: any) => p.attrs?.some((a: any) => !a.value || a.confidence === "low"));
    if (tab === "ok")     return allProducts.filter((p: any) => p.attrs?.every((a: any) => a.value && a.confidence !== "low"));
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
      <div className="bg-white border border-[#C4B5FD] border-l-[3px] border-l-violet-500 rounded-[10px] p-5">
        <div className="flex items-center gap-3 mb-2 flex-wrap">
          <span className="w-[22px] h-[22px] rounded-full bg-violet-100 text-violet-700 flex items-center justify-center text-[12px] font-bold flex-shrink-0">1</span>
          <div className="text-[13px] font-semibold text-violet-700">Substep A — AI extracts attribute values from raw Sunsky data</div>
          <div className="ml-auto flex gap-2 flex-shrink-0 flex-wrap">
            {needsReview > 0 && <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800">{needsReview} need review</span>}
            {ready > 0       && <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800">{ready} auto-confirmed</span>}
          </div>
        </div>
        <div className="text-[12px] text-[#64748B] mb-4">AI reads raw Sunsky title + spec block and extracts structured values. You confirm or correct.</div>

        {/* Legend */}
        <div className="flex gap-4 text-[12px] text-[#64748B] mb-4 flex-wrap">
          <span className="flex items-center gap-1.5">
            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-emerald-50 border border-emerald-200 text-emerald-700">high confidence</span>
            auto-confirmed, editable
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-amber-50 border border-amber-200 text-amber-700">low confidence</span>
            needs your review
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-red-50 border border-red-200 text-red-700">missing</span>
            AI could not extract — set manually
          </span>
        </div>

        {/* Tab bar */}
        {allProducts.length > 0 && (
          <div className="flex gap-1 bg-[#F1F5F9] rounded-lg p-1 mb-4 w-fit">
            {(["all","review","ok"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={cn("px-3 py-1.5 rounded-md text-[13px] font-medium transition-colors",
                  tab === t ? "bg-white text-violet-600 shadow-sm" : "text-[#64748B] hover:text-foreground"
                )}>
                {t === "all" ? `All (${allProducts.length})` : t === "review" ? `Needs attention (${needsReview})` : `Ready (${ready})`}
              </button>
            ))}
          </div>
        )}

        {/* Products table */}
        {allProducts.length > 0 ? (
          <div className="rounded-lg border border-[#E2E8F0] overflow-hidden mb-4">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="bg-[#F8FAFC]">
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.5px] border-b border-[#E2E8F0]">Product</th>
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.5px] border-b border-[#E2E8F0]">Extracted Attributes</th>
                </tr>
              </thead>
              <tbody>
                {displayed.slice(0, 30).map((p: any) => (
                  <tr key={p.id} className="border-b border-[#F1F5F9] last:border-0 hover:bg-[#FAFBFF]">
                    <td className="px-4 py-3 align-top">
                      <div className="font-medium text-[#0F172A]">{p.name}</div>
                      <div className="text-[12px] text-[#64748B] font-mono">{p.sku}</div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-wrap gap-1.5">
                        {(p.attrs ?? []).map((a: any, idx: number) => (
                          <span key={idx} className={cn(
                            "inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[12px] font-medium border-[1.5px]",
                            a.confidence === "high" ? "bg-emerald-50 border-emerald-200 text-emerald-700" :
                            a.confidence === "low"  ? "bg-amber-50 border-amber-200 text-amber-700" :
                            !a.value               ? "bg-red-50 border-red-200 text-red-700" :
                            "bg-[#F1F5F9] border-[#E2E8F0] text-[#475569]"
                          )}>
                            {a.attribute} : {a.value ?? "not found"}
                            {a.confidence && <span className={cn("text-[11px] ml-0.5", a.confidence === "low" ? "text-amber-600" : "")}>
                              {typeof a.score === "number" ? ` ${Math.round(a.score * 100)}%` : ""}
                            </span>}
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
          <div className="text-[13px] text-[#64748B] mb-4">No attribute data available yet.</div>
        )}

        {/* Bottom bar */}
        <div className="flex items-center justify-between pt-3 border-t border-[#E2E8F0]">
          <div className="text-[12px] text-[#64748B]">High-confidence extractions auto-confirm on next run if the same raw value is seen again</div>
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
        <div className="bg-[#EFF6FF] border border-[#BFDBFE] border-l-[3px] border-l-violet-500 rounded-lg px-4 py-3 text-[13px] text-[#1E40AF]">
          {newCats.length} Sunsky {newCats.length === 1 ? "category" : "categories"} in this batch {newCats.length === 1 ? "has" : "have"} no mapping rule. Assign a WooCommerce category and Attribute Profile for each.
        </div>
      )}

      {/* New categories */}
      {newCats.map((c: any) => {
        const s = sel[c.sunsky_cat] ?? { woo_cat_id: null, profile_id: null, save_as_rule: true };
        return (
          <div key={c.sunsky_cat} className="bg-white border border-[#E2E8F0] rounded-[10px] p-5">
            <div className="flex items-center gap-3 mb-4 flex-wrap">
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[12px] font-medium bg-amber-100 text-amber-800">Unmapped</span>
              <strong className="text-[15px]">{c.sunsky_cat}</strong>
              <span className="text-[12px] text-[#94A3B8]">{c.product_count} product{c.product_count !== 1 ? "s" : ""} in this batch</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-[12px] font-medium text-[#475569] mb-1.5">WooCommerce Category</label>
                <select
                  value={s.woo_cat_id ?? ""}
                  onChange={e => setSel(prev => ({ ...prev, [c.sunsky_cat]: { ...s, woo_cat_id: e.target.value ? parseInt(e.target.value) : null } }))}
                  className="w-full px-3 py-2 border border-[#E2E8F0] rounded-lg text-[13px] text-[#1E293B] bg-white focus:outline-none focus:border-violet-400"
                >
                  <option value="">Select category…</option>
                  {wooOptions.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
                {s.woo_cat_id && (
                  <div className="text-[11px] text-violet-600 mt-1">★ AI suggestion: {wooOptions.find(o => o.id === s.woo_cat_id)?.label}</div>
                )}
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[#475569] mb-1.5">Attribute Profile</label>
                <select
                  value={s.profile_id ?? ""}
                  onChange={e => setSel(prev => ({ ...prev, [c.sunsky_cat]: { ...s, profile_id: e.target.value ? parseInt(e.target.value) : null } }))}
                  className="w-full px-3 py-2 border border-[#E2E8F0] rounded-lg text-[13px] text-[#1E293B] bg-white focus:outline-none focus:border-violet-400"
                >
                  <option value="">— No profile —</option>
                  {profiles.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
            </div>
            <label className="flex items-start gap-2.5 cursor-pointer mt-2">
              <span className={cn(
                "w-4 h-4 min-w-[16px] border-2 rounded flex items-center justify-center mt-0.5 text-[11px] transition-colors",
                s.save_as_rule ? "bg-violet-600 border-violet-600 text-white" : "border-[#CBD5E1] bg-white"
              )} onClick={() => setSel(prev => ({ ...prev, [c.sunsky_cat]: { ...s, save_as_rule: !s.save_as_rule } }))}>
                {s.save_as_rule && "✓"}
              </span>
              <div>
                <div className="text-[13px] text-[#334155]">Save as permanent rule in Category Mapping</div>
                <div className="text-[12px] text-[#94A3B8] mt-0.5">Future pipelines with this Sunsky category will not pause again.</div>
              </div>
            </label>
            <button className="text-violet-600 text-[12px] mt-3 hover:underline">+ Create new WooCommerce category</button>
          </div>
        );
      })}

      {/* Already mapped */}
      {knownCats.length > 0 && (
        <div className="bg-[#F0FDF4] border border-[#BBF7D0] border-l-[3px] border-l-emerald-500 rounded-lg px-4 py-3 text-[13px] text-[#166534]">
          <strong>✓ Already mapped — applied automatically</strong><br />
          <span className="text-[12px] mt-0.5 block">
            {knownCats.map(c => `${c.sunsky_cat} → ${c.woo_cats?.[0]?.name ?? "?"}`).join(" · ")}
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

  useEffect(() => {
    fetch(`/api/pipelines/${pl.id}/content-data`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(setData)
      .catch(() => toast({ title: "Failed to load content data", variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [pl.id]);

  const allProducts: any[]    = data?.products ?? [];
  const needsAttention        = allProducts.filter(p => p.needs_attention && !excluded.has(p.id));
  const ready                 = allProducts.filter(p => !p.needs_attention && !excluded.has(p.id));

  const displayed = useMemo(() => {
    const active = allProducts.filter(p => !excluded.has(p.id));
    if (tab === "attention") return active.filter(p => p.needs_attention);
    if (tab === "ready")     return active.filter(p => !p.needs_attention);
    return active;
  }, [allProducts, tab, excluded]);

  const handleUploadAll = async () => {
    setSaving(true);
    try {
      const r = await fetch(`/api/pipelines/${pl.id}/content-confirm`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Upload started", description: "Products are being uploaded to WooCommerce…" });
      onDone();
    } catch (e: any) {
      toast({ title: "Failed to start upload", description: e.message, variant: "destructive" });
    } finally { setSaving(false); }
  };

  if (loading) return <div className="flex items-center gap-2 py-8 text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  const readyCount = ready.length;

  return (
    <div className="space-y-4">
      {/* Substep B card */}
      <div className="bg-white border border-[#BBF7D0] border-l-[3px] border-l-emerald-500 rounded-[10px] p-5">
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <span className="w-[22px] h-[22px] rounded-full bg-emerald-100 text-emerald-700 flex items-center justify-center text-[12px] font-bold flex-shrink-0">2</span>
          <div className="text-[13px] font-semibold text-emerald-700">Substep B — Review generated content</div>
          <div className="ml-auto">
            <div className="flex gap-1 bg-[#F1F5F9] rounded-lg p-1">
              {(["all","attention","ready"] as const).map(t => (
                <button key={t} onClick={() => setTab(t)}
                  className={cn("px-3 py-1 rounded-md text-[13px] font-medium transition-colors",
                    tab === t ? "bg-white text-violet-600 shadow-sm" : "text-[#64748B] hover:text-foreground"
                  )}>
                  {t === "all" ? `All (${allProducts.length})` : t === "attention" ? `Needs attention (${needsAttention.length})` : `Ready (${ready.length})`}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Product rows */}
        <div className="space-y-2 mb-4">
          {displayed.slice(0, 50).map((p: any) => {
            const isExp = expanded.has(p.id);
            return (
              <div key={p.id} className="border border-[#E2E8F0] rounded-lg overflow-hidden">
                <div
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer bg-white hover:bg-[#FAFBFF]"
                  onClick={() => setExpanded(prev => { const s = new Set(prev); s.has(p.id) ? s.delete(p.id) : s.add(p.id); return s; })}
                >
                  {p.needs_attention
                    ? <span className="inline-flex px-2 py-0.5 rounded-full text-[12px] font-medium bg-amber-100 text-amber-800 flex-shrink-0">Needs attention</span>
                    : <span className="inline-flex px-2 py-0.5 rounded-full text-[12px] font-medium bg-emerald-100 text-emerald-800 flex-shrink-0">Ready</span>
                  }
                  <strong className="text-[13px]">{p.name}</strong>
                  <span className="text-[12px] text-[#64748B]">{p.sku}{p.price ? ` · $${p.price}` : ""}</span>
                  <span className="ml-auto text-[#94A3B8] text-[12px]">{isExp ? "▾" : "›"}</span>
                </div>
                {isExp && (
                  <div className="px-4 pb-4 bg-[#FAFBFF] border-t border-[#E2E8F0]">
                    <div className="grid grid-cols-1 gap-3 mt-3">
                      <div>
                        <label className="block text-[12px] font-medium text-[#475569] mb-1">Product Title</label>
                        <input defaultValue={p.name} className="w-full px-3 py-2 border border-[#E2E8F0] rounded-lg text-[13px] text-[#1E293B] bg-white focus:outline-none focus:border-violet-400" />
                      </div>
                      {p.description && (
                        <div>
                          <label className="block text-[12px] font-medium text-[#475569] mb-1">Description</label>
                          <textarea defaultValue={p.description} rows={3}
                            className="w-full px-3 py-2 border border-[#E2E8F0] rounded-lg text-[13px] text-[#1E293B] bg-white focus:outline-none focus:border-violet-400 resize-y" />
                        </div>
                      )}
                      {p.image_count > 0 && (
                        <div>
                          <label className="block text-[12px] font-medium text-[#475569] mb-1">Images</label>
                          <div className="flex gap-2 flex-wrap">
                            {Array.from({ length: p.image_count }).map((_, idx) => (
                              <div key={idx} className="w-11 h-11 bg-[#E2E8F0] border border-[#CBD5E1] rounded-lg flex items-center justify-center text-[10px] text-[#94A3B8]">
                                img{idx + 1}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="mt-3 pt-3 border-t border-[#E2E8F0] flex gap-2">
                      <button onClick={() => setExcluded(prev => { const s = new Set(prev); s.add(p.id); return s; })}
                        className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-white border border-[#E2E8F0] text-[#475569] hover:bg-[#F8FAFC]">
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
        <div className="flex items-center justify-between pt-3 border-t border-[#E2E8F0] flex-wrap gap-3">
          <div className="flex gap-2 flex-wrap">
            <button className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-white border border-[#E2E8F0] text-[#475569] hover:bg-[#F8FAFC]">Exclude selected</button>
            <button className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-white border border-[#E2E8F0] text-[#475569] hover:bg-[#F8FAFC]">Re-generate content</button>
            <button className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-white border border-[#E2E8F0] text-[#475569] hover:bg-[#F8FAFC]">Assign category</button>
          </div>
          <button onClick={handleUploadAll} disabled={saving || readyCount === 0}
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
        <div className="bg-white border border-[#E2E8F0] rounded-[10px] px-5 py-4">
          <div className="text-[12px] text-[#64748B] mb-1.5">Uploaded successfully</div>
          <div className="text-[28px] font-bold text-[#16A34A] leading-none tracking-[-1px]">{uploaded}</div>
        </div>
        <div className="bg-white border border-[#E2E8F0] rounded-[10px] px-5 py-4">
          <div className="text-[12px] text-[#64748B] mb-1.5">Failed</div>
          <div className="text-[28px] font-bold leading-none tracking-[-1px]" style={{ color: failed > 0 ? "#DC2626" : "#0F172A" }}>{failed}</div>
        </div>
        <div className="bg-white border border-[#E2E8F0] rounded-[10px] px-5 py-4">
          <div className="text-[12px] text-[#64748B] mb-1.5">Excluded</div>
          <div className="text-[28px] font-bold text-[#94A3B8] leading-none tracking-[-1px]">{excluded}</div>
        </div>
      </div>

      {/* Failed Products table */}
      {failed > 0 && (
        <div className="bg-white border border-[#FCA5A5] rounded-[10px] overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-[#FEE2E2]">
            <div className="text-[11px] font-semibold text-[#DC2626] uppercase tracking-[0.6px]">Failed Products ({failed})</div>
            <button className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-white border border-[#E2E8F0] text-[#475569] hover:bg-[#F8FAFC]">↺ Retry all failed</button>
          </div>
          {failedProducts.length > 0 ? (
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="bg-[#FFF5F5]">
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.5px]">SKU</th>
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.5px]">Product</th>
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.5px]">Error</th>
                  <th className="text-left px-4 py-2.5 text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.5px]">Action</th>
                </tr>
              </thead>
              <tbody>
                {failedProducts.map((p: any) => (
                  <tr key={p.id} className="border-t border-[#FEE2E2] hover:bg-[#FFF5F5]">
                    <td className="px-4 py-3 text-[12px] text-[#64748B] font-mono">{p.sku}</td>
                    <td className="px-4 py-3 font-medium">{p.name}</td>
                    <td className="px-4 py-3 text-[12px] text-[#DC2626]">{p.error_message ?? "Unknown error"}</td>
                    <td className="px-4 py-3"><button className="px-2.5 py-1 rounded-md text-[11px] bg-white border border-[#E2E8F0] text-[#475569] hover:bg-[#F8FAFC] cursor-pointer">Retry</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="px-5 py-4 text-[13px] text-[#64748B] italic">Check the log below for details on failed products.</div>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center gap-3 justify-end">
        <Link href="/products">
          <button className="px-4 py-2 rounded-lg bg-white border border-[#E2E8F0] text-[#475569] text-[13px] font-medium hover:bg-[#F8FAFC] transition-colors">
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
      <div className="bg-[#FFF5F5] border border-[#FCA5A5] border-l-[3px] border-l-red-500 rounded-lg px-4 py-3">
        <div className="flex items-start gap-2">
          <XCircle className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
          <div>
            <p className="font-semibold text-[#991B1B] text-[13px]">Pipeline {pl.status === "cancelled" ? "Cancelled" : "Failed"}</p>
            {pl.error_message && <p className="text-[12px] text-[#DC2626] mt-1 font-mono">{pl.error_message}</p>}
          </div>
        </div>
      </div>
      <div className="flex gap-3">
        <button onClick={() => onAction("continue")}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 text-[13px] font-medium transition-colors">
          <Play className="w-3.5 h-3.5 fill-current" /> Continue from last step
        </button>
        <button onClick={() => onAction("retry")}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-white border border-[#E2E8F0] text-[#475569] text-[13px] font-medium hover:bg-[#F8FAFC] transition-colors">
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
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-[#EDE9FE] text-[#5B21B6]"><span className="w-1.5 h-1.5 rounded-full bg-[#8B5CF6] inline-block" /> Running</span>;
  if (["enrich_review","review","content_review"].includes(status))
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-[#FEF3C7] text-[#92400E]"><span className="w-1.5 h-1.5 rounded-full bg-[#F59E0B] animate-pulse inline-block" /> Waiting for input</span>;
  if (status === "completed")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-[#DCFCE7] text-[#166534] text-[13px]"><span className="w-1.5 h-1.5 rounded-full bg-[#22C55E] inline-block" /> Completed</span>;
  if (status === "failed")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-[#FEE2E2] text-[#991B1B]"><span className="w-1.5 h-1.5 rounded-full bg-[#EF4444] inline-block" /> Failed</span>;
  if (status === "cancelled")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-[#F1F5F9] text-[#475569]">Cancelled</span>;
  if (status === "queued")
    return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-[#F1F5F9] text-[#475569]"><span className="w-1.5 h-1.5 rounded-full bg-[#94A3B8] inline-block" /> Queued</span>;
  return <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium bg-[#F1F5F9] text-[#475569]">{status}</span>;
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
  const [pl, setPl]           = useState<Pipeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const pollRef               = useRef<ReturnType<typeof setInterval> | null>(null);
  const [logOpen, setLogOpen] = useState(true);

  const storeMap = Object.fromEntries((stores ?? []).map(s => [s.id, s.name]));

  const fetchPipeline = useCallback(async () => {
    try {
      const r = await fetch(`/api/pipelines/${plId}`);
      if (!r.ok) throw new Error(`Pipeline not found (${r.status})`);
      setPl(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [plId]);

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

  if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 animate-spin text-violet-500" /></div>;

  if (error || !pl) return (
    <div className="space-y-4 p-6">
      <Link href="/pipelines"><button className="flex items-center gap-1.5 text-[13px] text-[#64748B] hover:text-[#1E293B]"><ArrowLeft className="w-4 h-4" /> All Runs</button></Link>
      <div className="bg-[#FEE2E2] border border-[#FCA5A5] rounded-[10px] p-6 text-[#991B1B]">{error ?? "Pipeline not found"}</div>
    </div>
  );

  const storeName  = storeMap[pl.store_id];
  const storeColor = getStoreColor(pl.store_id);

  return (
    <div className="space-y-5 max-w-4xl">
      {/* Breadcrumb */}
      <Link href="/pipelines">
        <button className="flex items-center gap-1.5 text-[12px] text-[#64748B] hover:text-[#1E293B] transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> All Runs
        </button>
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="text-[12px] text-[#64748B] mb-1">{pl.pl_id} · {storeName ?? `Store #${pl.store_id}`}{pl.status === "completed" && pl.updated_at ? ` · ${format(new Date(pl.updated_at), "MMM d, yyyy — hh:mm aa")}` : ""}</div>
          <h1 className="text-[22px] font-bold text-[#0F172A] tracking-[-0.3px]">{pageTitle(pl.status)}</h1>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={pl.status} />
          {(isLive || isReview) && (
            <button onClick={() => handleAction("cancel")}
              className="px-3 py-1.5 rounded-lg bg-white border border-[#E2E8F0] text-[#EF4444] text-[12px] font-medium hover:bg-[#FFF5F5] transition-colors flex items-center gap-1.5">
              <Square className="w-3 h-3 fill-current" /> Cancel
            </button>
          )}
          <button onClick={fetchPipeline}
            className="px-3 py-1.5 rounded-lg bg-white border border-[#E2E8F0] text-[#475569] text-[12px] font-medium hover:bg-[#F8FAFC] transition-colors flex items-center gap-1.5">
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
        </div>
      </div>

      {/* Stage Trail */}
      <div className="bg-white border border-[#E2E8F0] rounded-[10px] p-4">
        <div className="text-[11px] font-semibold text-[#64748B] uppercase tracking-[0.6px] mb-3">Pipeline Stages</div>
        <StageTrail pl={pl} />
      </div>

      {/* State-specific content */}
      {pl.status === "running" && <RunningSection pl={pl} />}

      {pl.status === "queued" && (
        <div className="bg-white border border-[#E2E8F0] rounded-[10px] p-5 flex items-start gap-3">
          <Clock className="w-5 h-5 text-[#94A3B8] mt-0.5 flex-shrink-0" />
          <div>
            <p className="font-semibold text-[#0F172A]">Queued</p>
            <p className="text-[13px] text-[#64748B] mt-1">This pipeline is waiting for another pipeline on the same store to complete. It will start automatically.</p>
          </div>
        </div>
      )}

      {pl.status === "enrich_review" && <EnrichReviewSection pl={pl} onDone={fetchPipeline} />}

      {pl.status === "review" && <CategoryReviewSection pl={pl} onDone={fetchPipeline} />}

      {pl.status === "content_review" && <ContentReviewSection pl={pl} onDone={fetchPipeline} />}

      {pl.status === "completed" && <CompletedSection pl={pl} plId={plId} />}

      {(pl.status === "failed" || pl.status === "cancelled") && <FailedSection pl={pl} onAction={handleAction} />}

      {/* Log Panel */}
      <div className="bg-white border border-[#E2E8F0] rounded-[10px] overflow-hidden">
        <button onClick={() => setLogOpen(x => !x)}
          className="w-full flex items-center gap-2 px-4 py-3 border-b border-[#E2E8F0] hover:bg-[#F8FAFC] text-left">
          <Terminal className="w-4 h-4 text-[#94A3B8]" />
          <span className="text-[13px] font-medium text-[#475569]">Pipeline Log</span>
          {isLive && <span className="flex items-center gap-1 text-[12px] text-violet-600 ml-2"><span className="w-1.5 h-1.5 bg-violet-500 rounded-full animate-pulse inline-block" /> Live</span>}
          <span className="ml-auto text-[#94A3B8] text-[12px]">{logOpen ? "▲" : "▼"}</span>
        </button>
        {logOpen && <LogPanel plId={plId} isLive={isLive} />}
      </div>
    </div>
  );
}
