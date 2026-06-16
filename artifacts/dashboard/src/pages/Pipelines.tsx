import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useLocation } from "wouter";
import {
  Plus, ChevronDown, ChevronRight, CheckCircle2, XCircle,
  Loader2, Clock, AlertTriangle, Square, Play, RefreshCw,
  RotateCcw, Eye, Info, ChevronUp, Activity, Layers, Tag,
  Check, X as XIcon, ChevronLeft
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
  running:       { icon: Loader2,       cls: "bg-primary/10 text-primary border-primary/25",               dot: "bg-primary animate-pulse",        label: "Running" },
  queued:        { icon: Clock,         cls: "bg-secondary text-muted-foreground border-border",            dot: "bg-muted-foreground",             label: "Queued" },
  review:        { icon: Eye,           cls: "bg-amber-500/10 text-amber-400 border-amber-500/25",          dot: "bg-amber-400 animate-pulse",      label: "Review" },
  enrich_review: { icon: Layers,        cls: "bg-orange-500/10 text-orange-400 border-orange-500/25",      dot: "bg-orange-400 animate-pulse",     label: "Enrich Review" },
  completed:     { icon: CheckCircle2,  cls: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",   dot: "bg-emerald-400",                  label: "Completed" },
  failed:        { icon: XCircle,       cls: "bg-red-500/10 text-red-400 border-red-500/25",               dot: "bg-red-400",                      label: "Failed" },
  cancelled:     { icon: Square,        cls: "bg-orange-500/10 text-orange-400 border-orange-500/25",      dot: "bg-orange-400",                   label: "Cancelled" },
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
  process:       "Processing",
  enrich:        "Extracting Attrs",
  generate:      "Generating",
  review:        "Under Review",
  upload:        "Uploading",
  sync:          "Syncing",
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
// Category Map Panel — types and helpers
// ─────────────────────────────────────────────────────────────────────────────

interface WooOpt { id: number; name: string; parent_id: number }
interface WooCatEntry { id: number; name: string }
interface AttrProfileSummary { id: number; name: string; description: string | null; }
interface CategoryRow {
  sunsky_cat: string;
  product_count: number;
  woo_cats: WooCatEntry[];
  primary_woo_cat_id: number | null;
  profile_id: number | null;
  is_new: boolean;
  times_used: number;
}
interface RowSel {
  woo_cats: WooCatEntry[];
  primary_id: number | null;
  profile_id: number | null;
  save_as_rule: boolean;
}
interface TreeNode { opt: WooOpt; children: TreeNode[]; depth: number }

function buildCatTree(opts: WooOpt[]): TreeNode[] {
  const byId = new Map<number, TreeNode>();
  for (const o of opts) byId.set(o.id, { opt: o, children: [], depth: 0 });
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const pid = node.opt.parent_id;
    if (pid && byId.has(pid)) byId.get(pid)!.children.push(node);
    else roots.push(node);
  }
  function sortAndDepth(nodes: TreeNode[], d: number) {
    nodes.sort((a, b) => a.opt.name.localeCompare(b.opt.name));
    for (const n of nodes) { n.depth = d; sortAndDepth(n.children, d + 1); }
  }
  sortAndDepth(roots, 0);
  return roots;
}

// WooCommerce category checkbox tree widget
function WooCatTree({ tree, selected, primaryId, onToggle, onSetPrimary }: {
  tree: TreeNode[];
  selected: WooCatEntry[];
  primaryId: number | null;
  onToggle: (opt: WooOpt) => void;
  onSetPrimary: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const selectedIds = useMemo(() => new Set(selected.map(c => c.id)), [selected]);

  function toggleExpand(id: number) {
    setExpanded(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });
  }

  function renderNode(node: TreeNode): React.ReactNode {
    const isChecked = selectedIds.has(node.opt.id);
    const isPrimary = node.opt.id === primaryId;
    const hasKids = node.children.length > 0;
    const isOpen = expanded.has(node.opt.id);
    return (
      <div key={node.opt.id}>
        <div
          className="flex items-center gap-1.5 py-1 px-1 rounded hover:bg-secondary/50 group"
          style={{ paddingLeft: `${node.depth * 14 + 4}px` }}
        >
          {hasKids ? (
            <button onClick={() => toggleExpand(node.opt.id)} className="w-3.5 h-3.5 text-muted-foreground hover:text-foreground shrink-0">
              {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            </button>
          ) : <span className="w-3.5 shrink-0" />}
          <input
            type="checkbox"
            checked={isChecked}
            onChange={() => onToggle(node.opt)}
            className="w-3.5 h-3.5 rounded shrink-0 cursor-pointer accent-primary"
          />
          <span
            className={cn("text-xs cursor-pointer flex-1 min-w-0 truncate",
              isChecked ? (isPrimary ? "text-emerald-400 font-medium" : "text-blue-400") : "text-foreground"
            )}
            onClick={() => onToggle(node.opt)}
          >
            {node.opt.name}
          </span>
          {isChecked && !isPrimary && (
            <button
              onClick={() => onSetPrimary(node.opt.id)}
              className="text-[10px] text-blue-400/70 hover:text-emerald-400 px-1 shrink-0 transition-colors"
            >
              Set primary
            </button>
          )}
          {isChecked && isPrimary && (
            <span className="text-[10px] text-emerald-400 shrink-0 px-1">Primary</span>
          )}
        </div>
        {hasKids && isOpen && <div>{node.children.map(renderNode)}</div>}
      </div>
    );
  }

  if (tree.length === 0) return (
    <div className="p-3 text-xs text-muted-foreground italic">No WooCommerce categories synced. Sync from the Stores page first.</div>
  );
  return (
    <div className="max-h-52 overflow-y-auto bg-black/20 rounded-lg border border-border/30 p-1">
      {tree.map(renderNode)}
    </div>
  );
}

// Result summary — shows the final WooCommerce assignment in plain language
function CatResultSummary({ rowSel, wooById }: { rowSel: RowSel; wooById: Map<number, WooOpt> }) {
  if (!rowSel.woo_cats.length) return null;

  function getPath(id: number): string {
    const parts: string[] = [];
    let cur = wooById.get(id);
    while (cur) {
      parts.unshift(cur.name);
      cur = cur.parent_id ? wooById.get(cur.parent_id) : undefined;
    }
    return parts.join(" › ") || String(id);
  }

  const primary = rowSel.woo_cats.find(c => c.id === rowSel.primary_id);
  const others  = rowSel.woo_cats.filter(c => c.id !== rowSel.primary_id);
  return (
    <div className="mt-1.5 p-2 bg-secondary/30 rounded-lg border border-border/30 text-xs space-y-0.5">
      {primary && (
        <div><span className="text-muted-foreground">Primary: </span><span className="text-emerald-400 font-medium">{getPath(primary.id)}</span></div>
      )}
      {others.length > 0 && (
        <div><span className="text-muted-foreground">Also in: </span><span className="text-blue-400">{others.map(c => getPath(c.id)).join(", ")}</span></div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Category Map Panel (shown inside Review row)
// ─────────────────────────────────────────────────────────────────────────────

function CategoryMapPanel({ pl, onResumed }: { pl: Pipeline; onResumed: () => void }) {
  const { toast } = useToast();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [sel, setSel] = useState<Record<string, RowSel>>({});
  const [saving, setSaving] = useState(false);
  const [openTree, setOpenTree] = useState<string | null>(null);

  // "All Products" mode — one selection applied to every Sunsky category
  const [applyAll, setApplyAll] = useState(false);
  const [allSel, setAllSel] = useState<RowSel>({ woo_cats: [], primary_id: null, profile_id: null, save_as_rule: true });
  const [allTreeOpen, setAllTreeOpen] = useState(false);

  useEffect(() => {
    fetch(`/api/pipelines/${pl.id}/map-data`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => {
        setData(d);
        const init: Record<string, RowSel> = {};
        (d.categories ?? []).forEach((c: CategoryRow) => {
          const cats = c.woo_cats ?? [];
          init[c.sunsky_cat] = {
            woo_cats: cats,
            primary_id: c.primary_woo_cat_id ?? cats[0]?.id ?? null,
            profile_id: c.profile_id ?? null,
            save_as_rule: true,
          };
        });
        setSel(init);
      })
      .catch(() => toast({ title: "Failed to load category data", variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [pl.id]);

  const wooById = useMemo<Map<number, WooOpt>>(() => {
    const m = new Map<number, WooOpt>();
    (data?.woo_options ?? []).forEach((o: WooOpt) => m.set(o.id, o));
    return m;
  }, [data?.woo_options]);

  const wooTree = useMemo(() => buildCatTree(data?.woo_options ?? []), [data?.woo_options]);

  const cats: CategoryRow[] = data?.categories ?? [];
  const isStateA = cats.length > 0 && cats.every(c => !c.is_new);
  const newCats  = cats.filter(c => c.is_new);
  const knownCats = cats.filter(c => !c.is_new);

  function toggleWooCat(sunsky_cat: string, opt: WooOpt) {
    setSel(prev => {
      const row = prev[sunsky_cat] ?? { woo_cats: [], primary_id: null, profile_id: null, save_as_rule: true };
      const already = row.woo_cats.some(c => c.id === opt.id);
      let woo_cats: WooCatEntry[];
      let primary_id = row.primary_id;
      if (already) {
        woo_cats = row.woo_cats.filter(c => c.id !== opt.id);
        if (primary_id === opt.id) primary_id = woo_cats[0]?.id ?? null;
      } else {
        woo_cats = [...row.woo_cats, { id: opt.id, name: opt.name }];
        if (!primary_id) primary_id = opt.id;
      }
      return { ...prev, [sunsky_cat]: { ...row, woo_cats, primary_id } };
    });
  }

  function setPrimary(sunsky_cat: string, id: number) {
    setSel(prev => ({ ...prev, [sunsky_cat]: { ...prev[sunsky_cat], primary_id: id } }));
  }

  function toggleAllWooCat(opt: WooOpt) {
    setAllSel(prev => {
      const already = prev.woo_cats.some(c => c.id === opt.id);
      let woo_cats: WooCatEntry[];
      let primary_id = prev.primary_id;
      if (already) {
        woo_cats = prev.woo_cats.filter(c => c.id !== opt.id);
        if (primary_id === opt.id) primary_id = woo_cats[0]?.id ?? null;
      } else {
        woo_cats = [...prev.woo_cats, { id: opt.id, name: opt.name }];
        if (!primary_id) primary_id = opt.id;
      }
      return { ...prev, woo_cats, primary_id };
    });
  }

  const handleConfirmResume = async () => {
    setSaving(true);
    try {
      let mappings: any[];
      if (applyAll) {
        // Apply the same selection to every Sunsky category in this pipeline
        if (!allSel.woo_cats.length) {
          toast({ title: "Select at least one WooCommerce category first", variant: "destructive" });
          setSaving(false);
          return;
        }
        mappings = cats.map(c => ({
          sunsky_cat: c.sunsky_cat,
          woo_cats: allSel.woo_cats,
          primary_woo_cat_id: allSel.primary_id,
          profile_id: allSel.profile_id,
          save_as_rule: allSel.save_as_rule,
        }));
      } else {
        mappings = Object.entries(sel)
          .filter(([, v]) => v.woo_cats.length > 0)
          .map(([sunsky_cat, v]) => ({
            sunsky_cat,
            woo_cats: v.woo_cats,
            primary_woo_cat_id: v.primary_id,
            profile_id: v.profile_id,
            save_as_rule: v.save_as_rule,
          }));
      }
      const r = await fetch(`/api/pipelines/${pl.id}/map-confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mappings }),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Category mappings saved — pipeline resumed" });
      onResumed();
    } catch (e: any) {
      toast({ title: "Failed to confirm mappings", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return (
    <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
      <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading category data…
    </div>
  );

  if (!data || cats.length === 0) return (
    <div className="p-4 space-y-3">
      <p className="text-sm text-muted-foreground italic">No Sunsky categories found in this batch.</p>
      <button onClick={handleConfirmResume} disabled={saving}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-amber-500/20 disabled:opacity-50">
        {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3 fill-current" />}
        Resume Pipeline
      </button>
    </div>
  );

  return (
    <div className="p-4 space-y-4">
      {/* Header + mode toggle */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <Tag className="w-4 h-4 text-amber-400 shrink-0" />
          <span className="text-sm font-medium text-amber-400">Category Mapping</span>
          <span className="text-xs text-muted-foreground">{cats.length} Sunsky {cats.length === 1 ? "category" : "categories"}</span>
        </div>
        {/* Mode switcher */}
        <div className="flex rounded-lg border border-border/40 overflow-hidden text-xs shrink-0">
          <button
            onClick={() => setApplyAll(false)}
            className={cn(
              "px-3 py-1.5 font-medium transition-colors",
              !applyAll ? "bg-amber-500/15 text-amber-400" : "text-muted-foreground hover:text-foreground"
            )}
          >
            Per Category
          </button>
          <button
            onClick={() => setApplyAll(true)}
            className={cn(
              "px-3 py-1.5 font-medium transition-colors border-l border-border/40",
              applyAll ? "bg-amber-500/15 text-amber-400" : "text-muted-foreground hover:text-foreground"
            )}
          >
            All Products
          </button>
        </div>
      </div>

      {/* ── All Products mode ── */}
      {applyAll && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 space-y-0 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-amber-500/20 flex items-center gap-2">
            <Tag className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-xs font-semibold text-amber-400">Apply one category to all {cats.reduce((s, c) => s + c.product_count, 0)} products</span>
            <span className="text-xs text-muted-foreground ml-auto">{cats.length} Sunsky {cats.length === 1 ? "category" : "categories"} → same WooCommerce destination</span>
          </div>

          {/* Selected chips */}
          {allSel.woo_cats.length > 0 && (
            <div className="flex flex-wrap gap-1.5 px-4 py-2.5 border-b border-amber-500/20">
              {allSel.woo_cats.map(c => (
                <span
                  key={c.id}
                  title={c.id === allSel.primary_id ? "Primary — click another to change" : "Click to set as primary"}
                  onClick={() => c.id !== allSel.primary_id && setAllSel(prev => ({ ...prev, primary_id: c.id }))}
                  className={cn(
                    "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border cursor-pointer select-none",
                    c.id === allSel.primary_id
                      ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                      : "bg-blue-500/15 text-blue-400 border-blue-500/30"
                  )}
                >
                  {c.id === allSel.primary_id && <span className="text-[10px]">★</span>}
                  {c.name}
                  <button
                    onClick={e => { e.stopPropagation(); toggleAllWooCat({ id: c.id, name: c.name, parent_id: 0 }); }}
                    className="ml-0.5 hover:text-red-400 leading-none"
                  >×</button>
                </span>
              ))}
            </div>
          )}

          {allSel.woo_cats.length > 0 && (
            <div className="px-4 pb-2 pt-1">
              <CatResultSummary rowSel={allSel} wooById={wooById} />
            </div>
          )}

          <div className="px-4 py-2.5">
            <button
              onClick={() => setAllTreeOpen(o => !o)}
              className="flex items-center gap-1 text-xs text-primary hover:text-primary/80"
            >
              {allTreeOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              {allTreeOpen ? "Close selector" : allSel.woo_cats.length > 0 ? "Edit selection" : "Select WooCommerce category"}
            </button>
            {allTreeOpen && (
              <div className="mt-2">
                <WooCatTree
                  tree={wooTree}
                  selected={allSel.woo_cats}
                  primaryId={allSel.primary_id}
                  onToggle={toggleAllWooCat}
                  onSetPrimary={id => setAllSel(prev => ({ ...prev, primary_id: id }))}
                />
              </div>
            )}
          </div>

          {/* Profile selector for "All Products" mode */}
          {(data?.profiles ?? []).length > 0 && (
            <div className="flex items-center gap-2 px-4 py-2.5 border-t border-amber-500/20">
              <span className="text-xs text-muted-foreground shrink-0">Attribute profile:</span>
              <select
                value={allSel.profile_id ?? ""}
                onChange={e => setAllSel(prev => ({ ...prev, profile_id: e.target.value ? Number(e.target.value) : null }))}
                className="flex-1 min-w-0 bg-background border border-border rounded-lg px-2 py-1 text-xs focus:outline-none focus:border-primary"
              >
                <option value="">— None —</option>
                {(data?.profiles ?? []).map((p: AttrProfileSummary) => (
                  <option key={p.id} value={p.id}>{p.name}{p.description ? ` · ${p.description}` : ""}</option>
                ))}
              </select>
              {allSel.profile_id && (
                <button onClick={() => setAllSel(prev => ({ ...prev, profile_id: null }))} className="text-muted-foreground hover:text-foreground shrink-0">
                  <XIcon className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          )}

          <div className="px-4 pb-3 flex items-center gap-1.5">
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                checked={allSel.save_as_rule}
                onChange={e => setAllSel(prev => ({ ...prev, save_as_rule: e.target.checked }))}
                className="w-3 h-3 rounded accent-primary"
              />
              Save as rule for each Sunsky category
            </label>
          </div>
        </div>
      )}

      {/* ── Per Category mode ── */}
      {!applyAll && (
        <>
      {/* State A — all known, one-click confirm */}
      {isStateA && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/25 text-xs text-emerald-400">
          <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <div className="font-medium">All {cats.length} {cats.length === 1 ? "category" : "categories"} already mapped</div>
            <div className="text-emerald-400/70 mt-0.5">Saved rules will be auto-applied. Confirm below to upload.</div>
          </div>
        </div>
      )}

      {/* State B — new categories banner */}
      {!isStateA && newCats.length > 0 && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-amber-500/10 border border-amber-500/25 text-xs text-amber-400">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <div className="font-medium">{newCats.length} new {newCats.length === 1 ? "category" : "categories"} need mapping</div>
            {knownCats.length > 0 && (
              <div className="text-amber-400/70 mt-0.5">{knownCats.length} known {knownCats.length === 1 ? "category" : "categories"} auto-applied from saved rules.</div>
            )}
          </div>
        </div>
      )}

      {/* Known categories — read-only summary */}
      {knownCats.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
            Auto-mapped ({knownCats.length})
          </div>
          {knownCats.map((cat: CategoryRow) => {
            const rowSel = sel[cat.sunsky_cat];
            const primary = rowSel?.woo_cats?.find(c => c.id === rowSel.primary_id);
            const others  = rowSel?.woo_cats?.filter(c => c.id !== rowSel.primary_id) ?? [];
            const assignedProfile = rowSel?.profile_id
              ? (data?.profiles ?? []).find((p: AttrProfileSummary) => p.id === rowSel.profile_id)
              : null;
            return (
              <div key={cat.sunsky_cat} className="flex items-start gap-2 p-2 rounded-lg bg-secondary/30 border border-border/30 text-xs">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <span className="font-mono text-foreground">{cat.sunsky_cat}</span>
                  <span className="text-muted-foreground ml-2">({cat.product_count} products)</span>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {primary && (
                      <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/25 text-[11px]">
                        ★ {primary.name}
                      </span>
                    )}
                    {others.map(c => (
                      <span key={c.id} className="px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 border border-blue-500/25 text-[11px]">
                        {c.name}
                      </span>
                    ))}
                    {!rowSel?.woo_cats?.length && (
                      <span className="text-muted-foreground italic text-[11px]">no mapping</span>
                    )}
                    {assignedProfile && (
                      <span className="px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-400 border border-violet-500/25 text-[11px]">
                        ⊞ {assignedProfile.name}
                      </span>
                    )}
                  </div>
                </div>
                {cat.times_used > 0 && (
                  <span className="text-[10px] text-muted-foreground shrink-0">used {cat.times_used}×</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* New categories — interactive checkbox tree */}
      {newCats.length > 0 && (
        <div className="space-y-3">
          <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
            Assign WooCommerce categories ({newCats.length} new)
          </div>
          {newCats.map((cat: CategoryRow) => {
            const rowSel = sel[cat.sunsky_cat] ?? { woo_cats: [], primary_id: null, profile_id: null, save_as_rule: true };
            const isOpen = openTree === cat.sunsky_cat;
            const profiles: AttrProfileSummary[] = data?.profiles ?? [];
            const setProfileId = (pid: number | null) =>
              setSel(prev => ({ ...prev, [cat.sunsky_cat]: { ...(prev[cat.sunsky_cat] ?? { woo_cats: [], primary_id: null, profile_id: null, save_as_rule: true }), profile_id: pid } }));
            return (
              <div key={cat.sunsky_cat} className="rounded-xl border border-border/40 overflow-hidden">
                {/* Row header */}
                <div className="flex items-center gap-2 px-3 py-2 bg-secondary/30">
                  <span className="font-mono text-xs text-foreground flex-1 min-w-0 truncate">{cat.sunsky_cat}</span>
                  <span className="text-xs text-muted-foreground shrink-0">{cat.product_count} products</span>
                  <label className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer shrink-0">
                    <input
                      type="checkbox"
                      checked={rowSel.save_as_rule}
                      onChange={e => setSel(prev => ({ ...prev, [cat.sunsky_cat]: { ...prev[cat.sunsky_cat], save_as_rule: e.target.checked } }))}
                      className="w-3 h-3 rounded accent-primary"
                    />
                    Save as rule
                  </label>
                </div>

                {/* Panel B — Attribute Profile selector */}
                {profiles.length > 0 && (
                  <div className="flex items-center gap-2 px-3 py-2 border-t border-border/20 bg-secondary/10">
                    <span className="text-xs text-muted-foreground shrink-0">Attribute profile:</span>
                    <select
                      value={rowSel.profile_id ?? ""}
                      onChange={e => setProfileId(e.target.value ? Number(e.target.value) : null)}
                      className="flex-1 min-w-0 bg-background border border-border rounded-lg px-2 py-1 text-xs focus:outline-none focus:border-primary"
                    >
                      <option value="">— None —</option>
                      {profiles.map(p => (
                        <option key={p.id} value={p.id}>{p.name}{p.description ? ` · ${p.description}` : ""}</option>
                      ))}
                    </select>
                    {rowSel.profile_id && (
                      <button onClick={() => setProfileId(null)} className="text-muted-foreground hover:text-foreground shrink-0">
                        <XIcon className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                )}

                {/* Selected category chips */}
                {rowSel.woo_cats.length > 0 && (
                  <div className="flex flex-wrap gap-1 px-3 py-2 bg-secondary/10 border-t border-border/20">
                    {rowSel.woo_cats.map(c => (
                      <span
                        key={c.id}
                        title={c.id === rowSel.primary_id ? "Primary — click another to change" : "Click to set as primary"}
                        onClick={() => c.id !== rowSel.primary_id && setPrimary(cat.sunsky_cat, c.id)}
                        className={cn(
                          "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border cursor-pointer select-none",
                          c.id === rowSel.primary_id
                            ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                            : "bg-blue-500/15 text-blue-400 border-blue-500/30"
                        )}
                      >
                        {c.id === rowSel.primary_id && <span className="text-[10px]">★</span>}
                        {c.name}
                        <button
                          onClick={e => { e.stopPropagation(); toggleWooCat(cat.sunsky_cat, { id: c.id, name: c.name, parent_id: 0 }); }}
                          className="ml-0.5 hover:text-red-400 leading-none"
                        >×</button>
                      </span>
                    ))}
                  </div>
                )}

                {/* Result summary */}
                {rowSel.woo_cats.length > 0 && (
                  <div className="px-3 pb-2">
                    <CatResultSummary rowSel={rowSel} wooById={wooById} />
                  </div>
                )}

                {/* Tree expand / collapse */}
                <div className="px-3 pt-1 pb-2">
                  <button
                    onClick={() => setOpenTree(isOpen ? null : cat.sunsky_cat)}
                    className="flex items-center gap-1 text-xs text-primary hover:text-primary/80"
                  >
                    {isOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                    {isOpen ? "Close selector" : rowSel.woo_cats.length > 0 ? "Edit selection" : "Select WooCommerce categories"}
                  </button>
                  {isOpen && (
                    <div className="mt-2">
                      <WooCatTree
                        tree={wooTree}
                        selected={rowSel.woo_cats}
                        primaryId={rowSel.primary_id}
                        onToggle={opt => toggleWooCat(cat.sunsky_cat, opt)}
                        onSetPrimary={id => setPrimary(cat.sunsky_cat, id)}
                      />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
        </>
      )}

      {/* ── Pipeline Summary ── */}
      {(() => {
        const totalProducts = data?.total_products ?? cats.reduce((s: number, c: CategoryRow) => s + c.product_count, 0);
        if (applyAll) {
          const ready = allSel.woo_cats.length > 0;
          return (
            <div className="rounded-xl border border-border/40 bg-secondary/20 overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border/30 flex items-center justify-between">
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Pipeline Summary</span>
                {ready && (
                  <span className="flex items-center gap-1 text-xs text-emerald-400 font-medium">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Ready for Upload
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 divide-x divide-border/30">
                <div className="px-4 py-3">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-0.5">Products</div>
                  <div className="text-lg font-bold text-foreground">{totalProducts}</div>
                </div>
                <div className="px-4 py-3">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-0.5">Category Selected</div>
                  <div className={cn("text-lg font-bold", ready ? "text-emerald-400" : "text-amber-400")}>
                    {ready ? allSel.woo_cats.length : "None"}
                  </div>
                  {ready && <div className="text-[10px] text-muted-foreground">applied to all products</div>}
                </div>
              </div>
            </div>
          );
        }
        const autoMappedCats = knownCats.length;
        const autoMappedProds = knownCats.reduce((s: number, c: CategoryRow) => s + c.product_count, 0);
        const newAssigned = newCats.filter(c => (sel[c.sunsky_cat]?.woo_cats?.length ?? 0) > 0).length;
        const newUnmapped = newCats.length - newAssigned;
        const allReady = newUnmapped === 0;
        return (
          <div className="rounded-xl border border-border/40 bg-secondary/20 overflow-hidden">
            <div className="px-4 py-2.5 border-b border-border/30 flex items-center justify-between">
              <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Pipeline Summary</span>
              {allReady && (
                <span className="flex items-center gap-1 text-xs text-emerald-400 font-medium">
                  <CheckCircle2 className="w-3.5 h-3.5" /> Ready for Upload
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-y sm:divide-y-0 divide-border/30">
              {[
                { label: "Products", value: totalProducts, color: "" },
                { label: "Auto-Mapped", value: autoMappedCats, sub: `${autoMappedProds} products`, color: autoMappedCats > 0 ? "emerald" : "" },
                { label: "New Assigned", value: `${newAssigned}/${newCats.length}`, sub: "new categories", color: newCats.length === 0 ? "emerald" : newAssigned === newCats.length ? "emerald" : "blue" },
                { label: "Unmapped", value: newUnmapped, sub: newUnmapped === 0 ? "all covered" : "will skip cat", color: newUnmapped === 0 ? "emerald" : "amber" },
              ].map(stat => (
                <div key={stat.label} className="px-4 py-3">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-0.5">{stat.label}</div>
                  <div className={cn("text-lg font-bold",
                    stat.color === "emerald" ? "text-emerald-400" :
                    stat.color === "amber" ? "text-amber-400" :
                    stat.color === "blue" ? "text-blue-400" :
                    "text-foreground"
                  )}>{stat.value}</div>
                  {stat.sub && <div className="text-[10px] text-muted-foreground">{stat.sub}</div>}
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      {/* Confirm button */}
      <div className="flex items-center gap-3 pt-1 border-t border-border/30">
        <button
          onClick={handleConfirmResume}
          disabled={saving}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-amber-500/20 transition-colors disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3 fill-current" />}
          Confirm &amp; Resume
        </button>
        <span className="text-xs text-muted-foreground">
          {applyAll
            ? allSel.woo_cats.length > 0
              ? `"${allSel.woo_cats.find(c => c.id === allSel.primary_id)?.name ?? allSel.woo_cats[0]?.name}" → all products`
              : "Select a category first"
            : isStateA
              ? "All categories already saved — one click to upload"
              : `${newCats.filter(c => (sel[c.sunsky_cat]?.woo_cats?.length ?? 0) > 0).length}/${newCats.length} new categories assigned`}
        </span>
      </div>
    </div>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
// Enrich Review Panel (shown when status = enrich_review)
// ─────────────────────────────────────────────────────────────────────────────

function EnrichReviewPanel({ pl, onConfirmed }: { pl: Pipeline; onConfirmed: () => void }) {
  const { toast } = useToast();
  const [tab, setTab] = useState<"attrs" | "norm" | "variants">("attrs");
  const [enrichData, setEnrichData] = useState<any>(null);
  const [enrichError, setEnrichError] = useState<string | null>(null);
  const [groupsData, setGroupsData] = useState<any>(null);
  const [loadingEnrich, setLoadingEnrich] = useState(true);
  const [loadingGroups, setLoadingGroups] = useState(true);
  const [normEdits, setNormEdits] = useState<Record<string, Record<string, string>>>({});
  // nameEdits: keyed by attribute name (not row ID) so all rows for same attr share one rename
  const [nameEdits, setNameEdits] = useState<Record<string, string>>({});
  // bulkNormEdits: keyed by attribute name — applies to ALL products when set
  const [bulkNormEdits, setBulkNormEdits] = useState<Record<string, string>>({});
  const [bulkOpen, setBulkOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  // Unique attribute names across all products (order preserved)
  const uniqueAttrNames = useMemo<string[]>(() => {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const p of enrichData?.products ?? []) {
      for (const a of p.attrs ?? []) {
        if (!seen.has(a.attribute)) { seen.add(a.attribute); result.push(a.attribute); }
      }
    }
    return result;
  }, [enrichData]);

  useEffect(() => {
    setEnrichError(null);
    fetch(`/api/pipelines/${pl.id}/enrich-data`)
      .then(async (r) => {
        if (!r.ok) {
          const text = await r.text().catch(() => `HTTP ${r.status}`);
          throw new Error(text || `HTTP ${r.status}`);
        }
        return r.json();
      })
      .then(setEnrichData)
      .catch((e) => setEnrichError(e.message ?? "Failed to load attribute data"))
      .finally(() => setLoadingEnrich(false));

    fetch(`/api/pipelines/${pl.id}/variant-groups`)
      .then((r) => r.ok ? r.json() : Promise.reject())
      .then(setGroupsData)
      .catch(() => {})
      .finally(() => setLoadingGroups(false));
  }, [pl.id]);

  const confidenceColor = (c?: number) =>
    !c ? "text-muted-foreground" : c >= 0.85 ? "text-emerald-400" : c >= 0.65 ? "text-amber-400" : "text-red-400";

  const handleConfirm = async () => {
    setSaving(true);
    try {
      const attrs: any[] = [];
      (enrichData?.products ?? []).forEach((p: any) => {
        (p.attrs ?? []).forEach((a: any) => {
          // bulkNormEdits[attrName] wins > per-row edit > existing normalised > raw
          const normVal =
            bulkNormEdits[a.attribute] ??
            normEdits[a.id]?.value ??
            a.normalised_value ??
            a.raw_value;
          const wooAttrName = nameEdits[a.attribute]?.trim() || undefined;
          attrs.push({
            product_id: p.product_id,
            attribute: a.attribute,
            normalised_value: normVal,
            woo_attr_name: wooAttrName,
            confirmed: true,
          });
        });
      });

      const new_norm_entries: any[] = [];
      Object.entries(normEdits).forEach(([attrId, v]) => {
        const allProducts = enrichData?.products ?? [];
        for (const p of allProducts) {
          const a = p.attrs?.find((x: any) => String(x.id) === String(attrId));
          if (a && v.value && v.value !== a.raw_value) {
            const wooAttrName = nameEdits[a.attribute]?.trim() || undefined;
            new_norm_entries.push({
              attribute: a.attribute,
              raw_value: a.raw_value,
              woo_term: v.value,
              woo_attr_name: wooAttrName,
            });
          }
        }
      });

      // Persist bulk normalised value overrides (cover every raw_value for that attr)
      const enrichedAttrIds = new Set(new_norm_entries.map(e => `${e.attribute}||${e.raw_value}`));
      Object.entries(bulkNormEdits).forEach(([attrName, bulkVal]) => {
        if (!bulkVal?.trim()) return;
        const wooAttrName = nameEdits[attrName]?.trim() || undefined;
        for (const p of (enrichData?.products ?? [])) {
          for (const a of (p.attrs ?? [])) {
            if (a.attribute === attrName) {
              const key = `${a.attribute}||${a.raw_value}`;
              if (!enrichedAttrIds.has(key)) {
                enrichedAttrIds.add(key);
                new_norm_entries.push({
                  attribute: a.attribute,
                  raw_value: a.raw_value,
                  woo_term: bulkVal.trim(),
                  woo_attr_name: wooAttrName,
                });
              }
            }
          }
        }
      });

      // Also persist attr-name-only renames (even when value wasn't changed)
      Object.entries(nameEdits).forEach(([attrName, wooName]) => {
        if (!wooName?.trim() || wooName.trim() === attrName) return;
        const allProducts = enrichData?.products ?? [];
        for (const p of allProducts) {
          for (const a of (p.attrs ?? [])) {
            if (a.attribute === attrName) {
              const key = `${a.attribute}||${a.raw_value}`;
              if (!enrichedAttrIds.has(key)) {
                enrichedAttrIds.add(key);
                new_norm_entries.push({
                  attribute: a.attribute,
                  raw_value: a.raw_value,
                  woo_term: bulkNormEdits[a.attribute] ?? normEdits[a.id]?.value ?? a.normalised_value ?? a.raw_value,
                  woo_attr_name: wooName.trim(),
                });
              }
            }
          }
        }
      });

      const r = await fetch(`/api/pipelines/${pl.id}/enrich-confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ attrs, new_norm_entries }),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Attributes confirmed — pipeline continuing" });
      onConfirmed();
    } catch (e: any) {
      toast({ title: "Failed to confirm attributes", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const toggleGroup = async (groupId: number, confirmed: boolean) => {
    try {
      await fetch(`/api/pipelines/${pl.id}/variant-groups/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify([{ id: groupId, confirmed }]),
      });
      setGroupsData((prev: any) => ({
        ...prev,
        groups: (prev?.groups ?? []).map((g: any) => g.id === groupId ? { ...g, confirmed } : g),
      }));
    } catch (_) {}
  };

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-3 mb-2">
        <Layers className="w-4 h-4 text-orange-400" />
        <span className="text-sm font-medium text-orange-400">Attribute Review</span>
        <span className="text-xs text-muted-foreground">Review AI-extracted attributes, adjust normalisations, and confirm variant groups</span>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border/40">
        {([["attrs", "Attributes"], ["norm", "Normalisation"], ["variants", "Variant Groups"]] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={cn(
              "px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors",
              tab === key ? "border-orange-400 text-orange-400" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Attributes tab */}
      {tab === "attrs" && (
        loadingEnrich ? (
          <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading attributes…
          </div>
        ) : enrichError ? (
          <div className="rounded-xl border border-red-500/25 bg-red-500/10 p-4 space-y-2">
            <div className="flex items-center gap-2 text-red-400 text-sm font-medium">
              <AlertTriangle className="w-4 h-4 shrink-0" /> Failed to load attribute data
            </div>
            <pre className="text-xs text-red-400/70 whitespace-pre-wrap break-all font-mono max-h-32 overflow-y-auto">{enrichError}</pre>
            <p className="text-xs text-muted-foreground">
              If this mentions a missing column, run <code className="px-1 bg-secondary rounded font-mono">git pull && pm2 restart pipeline-api</code> on your server to apply the latest migration.
            </p>
          </div>
        ) : !enrichData?.products?.length ? (
          <div className="py-3 text-sm text-muted-foreground italic">No attributes extracted yet.</div>
        ) : (
          <div className="space-y-3">
            {/* ── Bulk Edit panel ── */}
            {uniqueAttrNames.length > 0 && (
              <div className="rounded-xl border border-orange-500/25 bg-orange-500/5 overflow-hidden">
                <button
                  onClick={() => setBulkOpen(o => !o)}
                  className="w-full flex items-center gap-2 px-4 py-2.5 text-xs font-semibold text-orange-400 hover:bg-orange-500/10 transition-colors"
                >
                  {bulkOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                  <Layers className="w-3.5 h-3.5" />
                  Bulk Edit — apply to ALL products
                  <span className="ml-auto text-[10px] font-normal text-muted-foreground">
                    {Object.keys(bulkNormEdits).filter(k => bulkNormEdits[k]?.trim()).length > 0
                      ? `${Object.keys(bulkNormEdits).filter(k => bulkNormEdits[k]?.trim()).length} overrides active`
                      : "set once, apply to every product"}
                  </span>
                </button>
                {bulkOpen && (
                  <div className="border-t border-orange-500/20">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="bg-secondary/30">
                          <th className="px-3 py-1.5 text-left text-[10px] font-medium text-muted-foreground/70 w-28">Sunsky Attr</th>
                          <th className="px-3 py-1.5 text-left text-[10px] font-medium text-orange-400/80 w-36">WooCommerce Name <span className="text-muted-foreground/50">(all products)</span></th>
                          <th className="px-3 py-1.5 text-left text-[10px] font-medium text-blue-400/80">Normalised Value Override <span className="text-muted-foreground/50">(all products)</span></th>
                          <th className="px-2 py-1.5 w-6" />
                        </tr>
                      </thead>
                      <tbody>
                        {uniqueAttrNames.map(attrName => {
                          const bulk = bulkNormEdits[attrName] ?? "";
                          return (
                            <tr key={attrName} className="border-t border-border/20">
                              <td className="px-3 py-1.5 text-muted-foreground w-28">{attrName}</td>
                              <td className="px-3 py-1.5 w-36">
                                <input
                                  type="text"
                                  placeholder={nameEdits[attrName] || attrName}
                                  value={nameEdits[attrName] ?? ""}
                                  onChange={e => setNameEdits(prev => ({ ...prev, [attrName]: e.target.value }))}
                                  className="w-full bg-transparent border-b border-orange-400/25 focus:border-orange-400/70 outline-none py-0.5 text-orange-300 placeholder:text-muted-foreground/40 text-xs"
                                />
                              </td>
                              <td className="px-3 py-1.5">
                                <input
                                  type="text"
                                  placeholder="Leave blank to keep per-product values"
                                  value={bulk}
                                  onChange={e => setBulkNormEdits(prev => ({ ...prev, [attrName]: e.target.value }))}
                                  className={cn(
                                    "w-full bg-transparent border-b outline-none py-0.5 text-xs placeholder:text-muted-foreground/40",
                                    bulk ? "border-blue-400/60 text-blue-300" : "border-blue-400/20 focus:border-blue-400/60 text-blue-300"
                                  )}
                                />
                              </td>
                              <td className="px-2 py-1.5">
                                {bulk && (
                                  <button
                                    onClick={() => setBulkNormEdits(prev => { const n = { ...prev }; delete n[attrName]; return n; })}
                                    className="text-muted-foreground hover:text-red-400 text-[10px]"
                                    title="Clear override"
                                  >×</button>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    <div className="px-4 py-2 text-[10px] text-muted-foreground border-t border-orange-500/15">
                      WooCommerce Name is shared across all rows for that attribute. Normalised Value override replaces per-product values when set. Leave blank to keep individual edits below.
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Per-product attribute list */}
            <div className="space-y-3 max-h-72 overflow-y-auto pr-1">
            {enrichData.products.map((p: any) => (
              <div key={p.product_id} className="rounded-xl border border-border/30 bg-secondary/20 overflow-hidden">
                <div className="px-3 py-2 bg-secondary/40 text-xs font-medium text-foreground truncate">
                  {p.product_sku && <span className="text-muted-foreground mr-2 font-mono">{p.product_sku}</span>}
                  {p.product_name}
                </div>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-t border-border/20 bg-secondary/20">
                      <th className="px-3 py-1 text-left text-[10px] font-medium text-muted-foreground/70 w-28">Sunsky Attr</th>
                      <th className="px-3 py-1 text-left text-[10px] font-medium text-orange-400/80 w-32">WooCommerce Name</th>
                      <th className="px-3 py-1 text-left text-[10px] font-medium text-muted-foreground/70">Raw Value</th>
                      <th className="px-3 py-1 text-left text-[10px] font-medium text-blue-400/80 w-32">Normalised Value</th>
                      <th className="px-2 py-1 text-right text-[10px] font-medium text-muted-foreground/70 w-10">Conf.</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(p.attrs ?? []).map((a: any) => (
                      <tr key={a.id} className={cn("border-t border-border/20", bulkNormEdits[a.attribute] ? "opacity-60" : "")}>
                        <td className="px-3 py-1.5 text-muted-foreground w-28 shrink-0">{a.attribute}</td>
                        <td className="px-3 py-1.5 w-32">
                          <input
                            type="text"
                            placeholder={a.woo_attr_name_suggest ?? a.attribute}
                            defaultValue={a.woo_attr_name ?? a.woo_attr_name_suggest ?? ""}
                            onChange={(e) => setNameEdits((prev) => ({ ...prev, [a.attribute]: e.target.value }))}
                            className="w-full bg-transparent border-b border-orange-400/25 focus:border-orange-400/70 outline-none py-0.5 text-orange-300 placeholder:text-muted-foreground/40 text-xs"
                          />
                        </td>
                        <td className="px-3 py-1.5 font-mono text-foreground">{a.raw_value}</td>
                        <td className="px-3 py-1.5 w-32">
                          <input
                            type="text"
                            placeholder={a.norm_suggestion ?? a.raw_value}
                            defaultValue={a.normalised_value ?? a.norm_suggestion ?? ""}
                            onChange={(e) => setNormEdits((prev) => ({ ...prev, [a.id]: { value: e.target.value } }))}
                            className="w-full bg-transparent border-b border-blue-400/25 focus:border-blue-400/70 outline-none py-0.5 text-blue-300 placeholder:text-muted-foreground/40 text-xs"
                          />
                        </td>
                        <td className={cn("px-2 py-1.5 text-right tabular-nums text-[10px] w-10", confidenceColor(a.confidence))}>
                          {a.confidence != null ? `${Math.round(a.confidence * 100)}%` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
            </div>
          </div>
        )
      )}

      {/* Normalisation tab */}
      {tab === "norm" && (
        <div className="py-2 text-sm text-muted-foreground">
          <p className="mb-2 text-xs">
            Edit the "normalised" column in the Attributes tab to teach the pipeline how to translate raw Sunsky values into WooCommerce terms.
            Those mappings are saved permanently and applied to future runs.
          </p>
          <p className="text-xs text-muted-foreground/60">
            Norm dict size for this store: {enrichData?.norm_dict_size ?? 0} entries
          </p>
        </div>
      )}

      {/* Variant groups tab */}
      {tab === "variants" && (
        loadingGroups ? (
          <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading groups…
          </div>
        ) : !groupsData?.groups?.length ? (
          <div className="py-3 text-sm text-muted-foreground italic">No variant groups suggested — all products will be uploaded as individual items.</div>
        ) : (
          <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
            {groupsData.groups.map((g: any) => (
              <div key={g.id} className={cn(
                "rounded-xl border p-3 text-xs",
                g.confirmed ? "border-emerald-500/30 bg-emerald-500/5" : "border-border/30 bg-secondary/20"
              )}>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="font-medium text-foreground">{g.attribute}</span>
                  {g.pattern && <span className="text-muted-foreground font-mono truncate flex-1">{g.pattern}</span>}
                  <button
                    onClick={() => toggleGroup(g.id, !g.confirmed)}
                    className={cn(
                      "flex items-center gap-1 px-2 py-0.5 rounded-md border text-xs transition-colors",
                      g.confirmed
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-red-500/10 hover:text-red-400 hover:border-red-500/20"
                        : "border-border/40 text-muted-foreground hover:border-emerald-500/30 hover:text-emerald-400 hover:bg-emerald-500/10"
                    )}
                  >
                    {g.confirmed ? <><Check className="w-3 h-3" /> Confirmed</> : <><XIcon className="w-3 h-3" /> Confirm</>}
                  </button>
                </div>
                <div className="flex flex-wrap gap-1">
                  {g.products?.map((p: any) => (
                    <span key={p.id} className="px-1.5 py-0.5 rounded bg-secondary border border-border/30 font-mono text-muted-foreground">
                      {p.sku || p.name}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )
      )}

      <div className="flex items-center gap-2 pt-1 border-t border-border/30">
        <button
          onClick={handleConfirm}
          disabled={saving}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-orange-500/10 hover:bg-orange-500/20 text-orange-400 border border-orange-500/20 transition-colors disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
          Confirm Attributes &amp; Continue
        </button>
        <span className="text-xs text-muted-foreground">Pipeline will resume with Generate / Review after confirmation</span>
      </div>
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
  const [enrichPanelOpen, setEnrichPanelOpen] = useState(pl.status === "enrich_review");
  const [mapPanelOpen, setMapPanelOpen] = useState(pl.status === "review");
  const isLive = ["running", "review", "enrich_review"].includes(pl.status);

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

            {/* Enrich review panel toggle */}
            {pl.status === "enrich_review" && (
              <button
                onClick={() => setEnrichPanelOpen((x) => !x)}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-orange-500/10 hover:bg-orange-500/20 text-orange-400 border border-orange-500/20 transition-colors"
              >
                <Layers className="w-3 h-3" />
                {enrichPanelOpen ? "Hide Review" : "Review Attrs"}
              </button>
            )}

            {/* Category map panel toggle + plain Resume (review only) */}
            {pl.status === "review" && (
              <>
                <button
                  onClick={() => setMapPanelOpen((x) => !x)}
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-amber-500/20 transition-colors"
                >
                  <Tag className="w-3 h-3" />
                  {mapPanelOpen ? "Hide Map" : "Map Categories"}
                </button>
                <button
                  onClick={() => onAction("resume", pl.id)}
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-secondary hover:bg-secondary/80 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <Play className="w-3 h-3 fill-current" /> Skip &amp; Resume
                </button>
              </>
            )}

            {/* Cancel (running | queued | review | enrich_review) */}
            {["running", "queued", "review", "enrich_review"].includes(pl.status) && (
              <button
                onClick={() => onAction("cancel", pl.id)}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 transition-colors"
              >
                <Square className="w-3 h-3 fill-current" /> Cancel
              </button>
            )}

            {/* Continue / Retry (failed | cancelled) */}
            {["failed", "cancelled"].includes(pl.status) && (
              <>
                <button
                  onClick={() => onAction("continue", pl.id)}
                  title={
                    pl.current_step === "review" ? "Return to Category Mapping review" :
                    pl.current_step === "enrich" ? "Return to Attribute review" :
                    `Resume from ${pl.current_step ?? "last"} step — keeps same pipeline ID`
                  }
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 transition-colors"
                >
                  <Play className="w-3 h-3 fill-current" />
                  {pl.current_step === "review" ? "Back to Review" :
                   pl.current_step === "enrich"  ? "Back to Review" :
                   "Continue"}
                </button>
                <button
                  onClick={() => onAction("retry", pl.id)}
                  title="Start a brand-new pipeline from the beginning"
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-secondary hover:bg-secondary/80 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <RotateCcw className="w-3 h-3" /> Retry
                </button>
              </>
            )}
          </div>
        </td>
      </tr>

      {/* Enrich review panel */}
      {pl.status === "enrich_review" && enrichPanelOpen && (
        <tr className="border-b border-orange-500/10 bg-orange-500/5">
          <td colSpan={7} className="px-0">
            <EnrichReviewPanel pl={pl} onConfirmed={() => onAction("_refresh", pl.id)} />
          </td>
        </tr>
      )}

      {/* Enrich review banner (always visible when enrich_review) */}
      {pl.status === "enrich_review" && (
        <tr className="border-b border-orange-500/10 bg-orange-500/5">
          <td colSpan={7} className="px-4 py-2">
            <div className="flex items-center gap-3 text-sm">
              <Layers className="w-4 h-4 text-orange-400 shrink-0" />
              <span className="text-orange-400 font-medium">AI attribute extraction complete — review &amp; confirm to continue</span>
            </div>
          </td>
        </tr>
      )}

      {/* Review stats row */}
      {pl.status === "review" && pl.stats_json && (
        <tr className="border-b border-amber-500/10 bg-amber-500/5">
          <td colSpan={7} className="px-4 py-2">
            <div className="flex items-center gap-4 text-sm">
              <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
              <span className="text-amber-400 font-medium">Review required — map categories then Confirm &amp; Resume</span>
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

      {/* Category map panel */}
      {pl.status === "review" && mapPanelOpen && (
        <tr className="border-b border-amber-500/10 bg-amber-500/5">
          <td colSpan={7} className="px-0">
            <CategoryMapPanel pl={pl} onResumed={() => onAction("_refresh", pl.id)} />
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
    if (action === "_refresh") {
      await fetchPipelines();
      return;
    }
    try {
      let url = `/api/pipelines/${id}/${action}`;
      const r = await fetch(url, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      if (action === "retry") {
        const d = await r.json();
        toast({ title: "Retry started", description: `New pipeline ${d.pl_id} created from scratch` });
      } else if (action === "continue") {
        const d = await r.json();
        const step = d.current_step ?? d.status;
        const desc =
          d.status === "review"        ? "Returned to category mapping review" :
          d.status === "enrich_review" ? "Returned to attribute review" :
          d.status === "queued"        ? "Queued — will continue when slot is free" :
          `Continuing from '${step}' step`;
        toast({ title: `${d.pl_id} continuing`, description: desc });
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
