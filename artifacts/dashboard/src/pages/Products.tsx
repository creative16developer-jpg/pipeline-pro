import { useState, useEffect, useMemo } from "react";
import { useProducts, useProduct } from "@/hooks/use-products";
import { StatusBadge } from "@/components/StatusBadge";
import { Modal } from "@/components/Modal";
import {
  Search, Package, Image as ImageIcon, ExternalLink, Filter,
  Sparkles, Database, Tag, CheckCircle2, X, ChevronDown, ChevronRight,
  Loader2, AlertTriangle, Save
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";

// ── Source badge helper ────────────────────────────────────────────────────────
function SourceBadge({ source }: { source?: string }) {
  if (!source) return <span className="text-[10px] text-muted-foreground/50">—</span>;
  const isAI = source.startsWith("ai:");
  const isLogic = source === "logic" || source === "logic:fallback";
  const isSunsky = source === "sunsky";
  const label = isAI
    ? source.replace("ai:", "").toUpperCase()
    : isLogic
    ? "Logic"
    : isSunsky
    ? "Sunsky"
    : source;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full",
        isAI
          ? "bg-violet-500/15 text-violet-400 border border-violet-500/20"
          : isSunsky
          ? "bg-orange-500/15 text-orange-400 border border-orange-500/20"
          : "bg-secondary text-muted-foreground border border-border/40"
      )}
    >
      {isAI ? <Sparkles className="w-2.5 h-2.5" /> : <Database className="w-2.5 h-2.5" />}
      {label}
    </span>
  );
}

// ── Field row with Sunsky original vs generated value ─────────────────────────
function FieldRow({
  label,
  value,
  source,
  sunskyValue,
  mono = false,
}: {
  label: string;
  value?: string | null;
  source?: string;
  sunskyValue?: string | null;
  mono?: boolean;
}) {
  const isAI = source?.startsWith("ai:");
  const hasValue = value && value.trim().length > 0;
  const hasSunsky = sunskyValue && sunskyValue.trim().length > 0;
  const isDifferent = hasValue && hasSunsky && value !== sunskyValue;

  return (
    <div className="border border-border/40 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-secondary/30 border-b border-border/40">
        <span className="text-xs font-medium text-foreground">{label}</span>
        <SourceBadge source={source} />
      </div>
      <div className="p-3 space-y-2">
        {hasValue ? (
          <div>
            {isDifferent && isAI && (
              <p className="text-[10px] text-violet-400 mb-1 font-medium uppercase tracking-wide">AI Generated</p>
            )}
            <p
              className={cn(
                "text-sm text-foreground break-words",
                mono && "font-mono text-xs"
              )}
              dangerouslySetInnerHTML={
                label === "Description" || label === "Short Description"
                  ? { __html: value! }
                  : undefined
              }
            >
              {label !== "Description" && label !== "Short Description" ? value : undefined}
            </p>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground/50 italic">Not generated</p>
        )}
        {isDifferent && hasSunsky && (
          <div className="pt-2 border-t border-border/30">
            <p className="text-[10px] text-orange-400 mb-1 font-medium uppercase tracking-wide flex items-center gap-1">
              <Database className="w-2.5 h-2.5" /> Sunsky Original
            </p>
            <p className="text-xs text-muted-foreground/70 break-words line-clamp-3">{sunskyValue}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Mini WooCatTree for the Mapping tab ──────────────────────────────────────
interface WooOpt { id: number; name: string; parent_id: number }
interface WooCatEntry { id: number; name: string }
interface TreeNode { opt: WooOpt; children: TreeNode[]; depth: number }

function buildTree(opts: WooOpt[]): TreeNode[] {
  const byId = new Map<number, TreeNode>();
  for (const o of opts) byId.set(o.id, { opt: o, children: [], depth: 0 });
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const pid = node.opt.parent_id;
    if (pid && byId.has(pid)) byId.get(pid)!.children.push(node);
    else roots.push(node);
  }
  function sd(nodes: TreeNode[], d: number) {
    nodes.sort((a, b) => a.opt.name.localeCompare(b.opt.name));
    for (const n of nodes) { n.depth = d; sd(n.children, d + 1); }
  }
  sd(roots, 0);
  return roots;
}

function WooCatTree({ tree, selected, primaryId, onToggle, onSetPrimary }: {
  tree: TreeNode[];
  selected: WooCatEntry[];
  primaryId: number | null;
  onToggle: (opt: WooOpt) => void;
  onSetPrimary: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const selIds = useMemo(() => new Set(selected.map(c => c.id)), [selected]);

  function renderNode(node: TreeNode): React.ReactNode {
    const checked = selIds.has(node.opt.id);
    const isPrimary = node.opt.id === primaryId;
    const hasKids = node.children.length > 0;
    const isOpen = expanded.has(node.opt.id);
    return (
      <div key={node.opt.id}>
        <div
          className="flex items-center gap-1.5 py-0.5 px-1 rounded hover:bg-secondary/50 group"
          style={{ paddingLeft: `${node.depth * 14 + 4}px` }}
        >
          {hasKids
            ? <button onClick={() => setExpanded(p => { const s = new Set(p); s.has(node.opt.id) ? s.delete(node.opt.id) : s.add(node.opt.id); return s; })} className="w-3.5 h-3.5 shrink-0 text-muted-foreground">
                {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              </button>
            : <span className="w-3.5 shrink-0" />
          }
          <input type="checkbox" checked={checked} onChange={() => onToggle(node.opt)} className="w-3.5 h-3.5 rounded shrink-0 cursor-pointer accent-primary" />
          <span
            onClick={() => onToggle(node.opt)}
            className={cn("text-xs cursor-pointer flex-1 min-w-0 truncate",
              checked ? (isPrimary ? "text-emerald-400 font-medium" : "text-blue-400") : "text-foreground"
            )}
          >
            {node.opt.name}
          </span>
          {checked && !isPrimary && (
            <button onClick={() => onSetPrimary(node.opt.id)} className="text-[10px] text-muted-foreground hover:text-emerald-400 opacity-0 group-hover:opacity-100 px-1 shrink-0">Set primary</button>
          )}
          {checked && isPrimary && <span className="text-[10px] text-emerald-400 shrink-0 px-1">Primary</span>}
        </div>
        {hasKids && isOpen && <div>{node.children.map(renderNode)}</div>}
      </div>
    );
  }

  if (!tree.length) return <div className="p-3 text-xs text-muted-foreground italic">No WooCommerce categories — sync from Stores page first.</div>;
  return (
    <div className="max-h-52 overflow-y-auto bg-black/20 rounded-lg border border-border/30 p-1">
      {tree.map(renderNode)}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Products list page
// ─────────────────────────────────────────────────────────────────────────────

export default function Products() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<any>("");
  const [selectedProductId, setSelectedProductId] = useState<number | null>(null);

  const { data, isLoading } = useProducts({
    page,
    limit: 20,
    search: search || undefined,
    status: status || undefined,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between gap-4 items-start sm:items-center">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground">Product Catalog</h1>
          <p className="text-muted-foreground mt-1">Manage fetched and processed products.</p>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-card border border-border/50 p-4 rounded-2xl flex flex-col sm:flex-row gap-4 shadow-lg shadow-black/5">
        <div className="relative flex-1">
          <Search className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search by SKU or Name..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="w-full bg-background border border-border rounded-xl pl-10 pr-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-all text-sm"
          />
        </div>
        <div className="relative w-full sm:w-64">
          <Filter className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <select
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1); }}
            className="w-full bg-background border border-border rounded-xl pl-10 pr-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-all text-sm appearance-none"
          >
            <option value="">All Statuses</option>
            <option value="pending">Pending</option>
            <option value="processing">Processing</option>
            <option value="processed">Processed</option>
            <option value="uploaded">Uploaded</option>
            <option value="failed">Failed</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-secondary/40 border-b border-border/50 text-sm text-muted-foreground">
                <th className="p-4 font-medium">SKU</th>
                <th className="p-4 font-medium">Product</th>
                <th className="p-4 font-medium">Category</th>
                <th className="p-4 font-medium">Price</th>
                <th className="p-4 font-medium">Status</th>
                <th className="p-4 font-medium">Images</th>
                <th className="p-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {isLoading ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-muted-foreground">Loading products...</td>
                </tr>
              ) : data?.products?.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-12 text-center text-muted-foreground">
                    <Package className="w-12 h-12 mx-auto opacity-20 mb-3" />
                    No products found matching your criteria.
                  </td>
                </tr>
              ) : (
                data?.products?.map((product) => (
                  <tr key={product.id} className="hover:bg-secondary/20 transition-colors">
                    <td className="p-4 font-mono text-sm">{product.sku}</td>
                    <td className="p-4">
                      <div className="font-medium text-foreground max-w-xs truncate">{product.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5 flex items-center gap-2">
                        <span>Sunsky ID: {(product as any).sunskyId}</span>
                        {(product as any).contentSource &&
                          Object.values((product as any).contentSource as Record<string, string>).some(
                            (s) => s?.startsWith("ai:")
                          ) && (
                            <span className="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-violet-500/15 text-violet-400 border border-violet-500/20">
                              <Sparkles className="w-2.5 h-2.5" /> AI
                            </span>
                          )}
                        {(product as any).catSource === "manual" && (
                          <span className="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/20">
                            <Tag className="w-2.5 h-2.5" /> Manual Cat
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="p-4 text-sm">{(product as any).categoryId || '—'}</td>
                    <td className="p-4 text-sm font-medium">{product.price ? `$${product.price}` : '—'}</td>
                    <td className="p-4"><StatusBadge status={product.status} /></td>
                    <td className="p-4">
                      <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                        <ImageIcon className="w-4 h-4" /> {(product as any).imageCount}
                      </div>
                    </td>
                    <td className="p-4 text-right">
                      <button
                        onClick={() => setSelectedProductId(product.id)}
                        className="px-3 py-1.5 rounded-lg bg-secondary hover:bg-secondary/80 text-sm font-medium transition-colors"
                      >
                        Details
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {data && data.totalPages > 1 && (
          <div className="p-4 border-t border-border/50 bg-secondary/20 flex items-center justify-between">
            <span className="text-sm text-muted-foreground">
              Showing page <span className="font-medium text-foreground">{data.page}</span> of {data.totalPages}
            </span>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="px-4 py-2 rounded-xl border border-border bg-background hover:bg-secondary transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
              >
                Previous
              </button>
              <button
                disabled={page >= data.totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="px-4 py-2 rounded-xl border border-border bg-background hover:bg-secondary transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      <ProductDetailModal
        id={selectedProductId}
        onClose={() => setSelectedProductId(null)}
      />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Product Detail Modal — with Mapping tab
// ─────────────────────────────────────────────────────────────────────────────

function ProductDetailModal({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { data: product, isLoading, refetch } = useProduct(id as number) as any;
  const [tab, setTab] = useState<"content" | "raw" | "mapping">("content");

  // Mapping tab state
  const [wooOpts, setWooOpts] = useState<WooOpt[]>([]);
  const [wooLoading, setWooLoading] = useState(false);
  const [editCats, setEditCats] = useState<WooCatEntry[]>([]);
  const [editPrimary, setEditPrimary] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const { toast } = useToast();

  const wooTree = useMemo(() => buildTree(wooOpts), [wooOpts]);

  const src = (product as any)?.contentSource as Record<string, string> | null | undefined;
  const raw = (product as any)?.rawData as Record<string, any> | null | undefined;
  const sunskyDesc = raw?.description || raw?.desc || "";
  const storeId = (product as any)?.storeId as number | undefined;
  const catSource = (product as any)?.catSource as string | undefined;
  const manualCatsJson = (product as any)?.manualWooCatsJson as string | null;

  // When switching to mapping tab, load WooCommerce categories + init edit state
  useEffect(() => {
    if (tab !== "mapping" || !storeId) return;
    setWooLoading(true);
    fetch(`/api/stores/${storeId}/categories`)
      .then(r => r.ok ? r.json() : [])
      .then((cats: any[]) => {
        const list = Array.isArray(cats) ? cats : (cats.categories ?? []);
        setWooOpts(list.map((c: any) => ({ id: c.woo_id ?? c.id, name: c.name, parent_id: c.parent_id ?? 0 })));
      })
      .catch(() => {})
      .finally(() => setWooLoading(false));
  }, [tab, storeId]);

  // Init edit state from product's manual override (or empty)
  useEffect(() => {
    if (tab !== "mapping") return;
    if (manualCatsJson) {
      try {
        const cats = JSON.parse(manualCatsJson);
        setEditCats(cats);
        setEditPrimary((product as any)?.manualPrimaryWooCatId ?? cats[0]?.id ?? null);
        return;
      } catch { /* fall through */ }
    }
    setEditCats([]);
    setEditPrimary(null);
  }, [tab, manualCatsJson, product?.id]);

  const handleSave = async () => {
    if (!id) return;
    setSaving(true);
    try {
      const r = await fetch(`/api/products/${id}/categories`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ woo_cats: editCats, primary_woo_cat_id: editPrimary }),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Category override saved", description: "This product will use these categories during upload." });
      refetch?.();
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleClearOverride = async () => {
    if (!id) return;
    setClearing(true);
    try {
      const r = await fetch(`/api/products/${id}/categories/override`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Override cleared", description: "Product will use auto-mapping on next upload." });
      setEditCats([]);
      setEditPrimary(null);
      refetch?.();
    } catch (e: any) {
      toast({ title: "Failed", description: e.message, variant: "destructive" });
    } finally {
      setClearing(false);
    }
  };

  const toggleWoo = (opt: WooOpt) => {
    setEditCats(prev => {
      const has = prev.some(c => c.id === opt.id);
      const next = has ? prev.filter(c => c.id !== opt.id) : [...prev, { id: opt.id, name: opt.name }];
      if (has && editPrimary === opt.id) setEditPrimary(next[0]?.id ?? null);
      else if (!has && !editPrimary) setEditPrimary(opt.id);
      return next;
    });
  };

  return (
    <Modal isOpen={!!id} onClose={onClose} title={(product as any)?.name || "Product Details"} className="max-w-3xl">
      {isLoading ? (
        <div className="py-12 flex justify-center">
          <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      ) : product ? (
        <div className="space-y-5">
          {/* Meta grid */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-secondary/30 p-3 rounded-xl border border-border/50">
              <span className="text-xs text-muted-foreground block mb-1">SKU</span>
              <span className="font-mono font-medium text-sm">{product.sku}</span>
            </div>
            <div className="bg-secondary/30 p-3 rounded-xl border border-border/50">
              <span className="text-xs text-muted-foreground block mb-1">Status</span>
              <StatusBadge status={product.status} />
            </div>
            <div className="bg-secondary/30 p-3 rounded-xl border border-border/50">
              <span className="text-xs text-muted-foreground block mb-1">Price</span>
              <span className="font-medium text-sm">{product.price ? `$${product.price}` : "Unknown"}</span>
            </div>
            <div className="bg-secondary/30 p-3 rounded-xl border border-border/50">
              <span className="text-xs text-muted-foreground block mb-1">WooCommerce ID</span>
              <span className="font-medium text-sm flex items-center gap-2">
                {(product as any).wooProductId || "Not uploaded"}
                {(product as any).wooProductId && <ExternalLink className="w-4 h-4 text-muted-foreground" />}
              </span>
            </div>
          </div>

          {(product as any).errorMessage && (
            <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-sm">
              <p className="font-bold mb-1">Error</p>
              {(product as any).errorMessage}
            </div>
          )}

          {/* Tabs */}
          <div className="flex gap-1 p-1 bg-secondary/40 rounded-xl w-fit">
            {(["content", "raw", "mapping"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium transition-all capitalize",
                  tab === t
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {t === "mapping" && <Tag className="w-3.5 h-3.5" />}
                {t === "content" ? "Generated Content" : t === "raw" ? "Raw Data" : "Mapping"}
              </button>
            ))}
          </div>

          {tab === "content" ? (
            <div className="space-y-3">
              <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
                <span className="flex items-center gap-1">
                  <Sparkles className="w-3 h-3 text-violet-400" />
                  <span className="text-violet-400 font-medium">AI Generated</span>
                  — content produced by AI
                </span>
                <span className="flex items-center gap-1">
                  <Database className="w-3 h-3 text-orange-400" />
                  <span className="text-orange-400 font-medium">Sunsky</span>
                  — original Sunsky data
                </span>
              </div>

              <FieldRow label="Title" value={(product as any).name} source={src?.title || "sunsky"} sunskyValue={(product as any).name} />
              <FieldRow label="Description" value={(product as any).description} source={src?.description || "sunsky"} sunskyValue={sunskyDesc} />
              <FieldRow label="Short Description" value={(product as any).shortDescription} source={src?.short_description || "sunsky"} sunskyValue={sunskyDesc ? sunskyDesc.split(" ").slice(0, 30).join(" ") + "…" : undefined} />
              <div className="grid grid-cols-2 gap-3">
                <FieldRow label="Slug" value={(product as any).slug} source={src?.slug} mono />
                <FieldRow label="Tags" value={(product as any).tags} source={src?.tags} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <FieldRow label="Meta Title" value={(product as any).metaTitle} source={src?.meta_title} />
                <FieldRow label="Meta Description" value={(product as any).metaDescription} source={src?.meta_description} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <FieldRow label="Image Alt" value={(product as any).imageAlt} source={src?.image_alt} />
                <FieldRow label="Image Names" value={(product as any).imageNames} source={src?.image_names} mono />
              </div>
            </div>
          ) : tab === "raw" ? (
            <div className="bg-black/40 border border-border rounded-xl p-4 overflow-x-auto max-h-96">
              <pre className="text-xs font-mono text-muted-foreground">
                {JSON.stringify(raw || {}, null, 2)}
              </pre>
            </div>
          ) : (
            /* ── Mapping tab ── */
            <div className="space-y-4">
              {/* Source badge */}
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Category source:</span>
                  {catSource === "manual" ? (
                    <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/25">
                      <Tag className="w-3 h-3" /> Manual override
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-secondary text-muted-foreground border border-border/40">
                      <CheckCircle2 className="w-3 h-3" /> Auto (batch rules)
                    </span>
                  )}
                </div>
              </div>

              {/* Sunsky category */}
              {raw && (
                <div className="flex items-center gap-2 p-2.5 rounded-lg bg-secondary/30 border border-border/30 text-xs">
                  <span className="text-muted-foreground shrink-0">Sunsky category:</span>
                  <span className="font-mono text-foreground">
                    {raw.catName || raw.categoryName || raw.categoryId || raw.catId || "—"}
                  </span>
                </div>
              )}

              {/* Manual override notice */}
              {catSource === "manual" && (
                <div className="flex items-start gap-2 p-3 rounded-xl bg-amber-500/10 border border-amber-500/25 text-xs text-amber-400">
                  <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                  <div>
                    <div className="font-medium">Manual override active</div>
                    <div className="text-amber-400/70 mt-0.5">
                      These categories will always be used for this product — batch mapping rules are ignored.
                      Click "Clear Override" to return to auto-mapping.
                    </div>
                  </div>
                </div>
              )}

              {/* Selected chips */}
              {editCats.length > 0 && (
                <div>
                  <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">Assigned WooCommerce categories</div>
                  <div className="flex flex-wrap gap-1.5">
                    {editCats.map(c => (
                      <span
                        key={c.id}
                        onClick={() => c.id !== editPrimary && setEditPrimary(c.id)}
                        className={cn(
                          "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border cursor-pointer",
                          c.id === editPrimary
                            ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                            : "bg-blue-500/15 text-blue-400 border-blue-500/30"
                        )}
                      >
                        {c.id === editPrimary && <span className="text-[10px]">★</span>}
                        {c.name}
                        <button
                          onClick={e => { e.stopPropagation(); toggleWoo({ id: c.id, name: c.name, parent_id: 0 }); }}
                          className="ml-0.5 hover:text-red-400"
                        >×</button>
                      </span>
                    ))}
                  </div>
                  <div className="mt-1.5 text-[10px] text-muted-foreground">Click a category to set it as primary (★). Click × to remove.</div>
                </div>
              )}

              {/* Tree selector */}
              {!storeId ? (
                <div className="p-3 rounded-xl border border-border/40 text-xs text-muted-foreground italic">
                  No store linked to this product — cannot load WooCommerce categories.
                </div>
              ) : wooLoading ? (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-4 justify-center">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading WooCommerce categories…
                </div>
              ) : (
                <div>
                  <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">Select WooCommerce categories</div>
                  <WooCatTree
                    tree={wooTree}
                    selected={editCats}
                    primaryId={editPrimary}
                    onToggle={toggleWoo}
                    onSetPrimary={id => setEditPrimary(id)}
                  />
                </div>
              )}

              {/* Action buttons */}
              <div className="flex items-center gap-3 pt-2 border-t border-border/30">
                <button
                  onClick={handleSave}
                  disabled={saving || editCats.length === 0}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                  Save Override
                </button>
                {catSource === "manual" && (
                  <button
                    onClick={handleClearOverride}
                    disabled={clearing}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-border text-xs font-medium text-muted-foreground hover:text-foreground hover:border-red-500/40 hover:text-red-400 transition-colors disabled:opacity-50"
                  >
                    {clearing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <X className="w-3.5 h-3.5" />}
                    Clear Override
                  </button>
                )}
                <span className="text-xs text-muted-foreground">
                  {catSource === "manual" ? "Manual override — batch rules ignored" : "No override — auto-mapped from batch rules"}
                </span>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="py-8 text-center text-muted-foreground">Failed to load product.</div>
      )}
    </Modal>
  );
}
