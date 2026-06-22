import { useState, useEffect, useMemo, useRef } from "react";
import {
  Settings as SettingsIcon, Key, Eye, EyeOff, CheckCircle2,
  XCircle, Save, Trash2, Loader2, Info, Sparkles, ExternalLink,
  RefreshCw, Tag, Search, ChevronDown, ChevronRight, Edit2, X, Plus,
  Upload, FileSpreadsheet, AlertCircle
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

interface ProviderStatus {
  configured: boolean;
  source: "env" | "file" | "none";
  masked: string | null;
}

const PROVIDERS: Record<string, {
  label: string;
  description: string;
  envVar: string;
  docsUrl: string;
  docsLabel: string;
  color: string;
  bgColor: string;
  models: string[];
}> = {
  gemini: {
    label: "Google Gemini",
    description: "gemini-2.0-flash, gemini-1.5-pro and other Google models",
    envVar: "GEMINI_API_KEY",
    docsUrl: "https://aistudio.google.com/apikey",
    docsLabel: "Google AI Studio",
    color: "text-sky-400",
    bgColor: "bg-sky-500/10 border-sky-500/20",
    models: [
      "gemini-2.5-flash",
      "gemini-2.5-pro",
      "gemini-2.0-flash",
      "gemini-2.0-flash-lite",
      "gemini-1.5-pro",
      "gemini-1.5-flash",
      "gemini-1.5-flash-8b",
    ],
  },
  openai: {
    label: "OpenAI",
    description: "gpt-4o-mini, gpt-4o, gpt-4-turbo and other OpenAI models",
    envVar: "OPENAI_API_KEY",
    docsUrl: "https://platform.openai.com/api-keys",
    docsLabel: "OpenAI Platform",
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10 border-emerald-500/20",
    models: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
  },
  anthropic: {
    label: "Anthropic (Claude)",
    description: "claude-3-haiku, claude-3.5-sonnet and other Claude models",
    envVar: "ANTHROPIC_API_KEY",
    docsUrl: "https://console.anthropic.com/",
    docsLabel: "Anthropic Console",
    color: "text-amber-400",
    bgColor: "bg-amber-500/10 border-amber-500/20",
    models: ["claude-3-haiku-20240307", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
  },
};

const inputCls =
  "w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary font-mono placeholder:font-sans placeholder:text-muted-foreground";

// ─────────────────────────────────────────────────────────────────────────────
// Mini WooCatTree — used inside the inline edit row
// ─────────────────────────────────────────────────────────────────────────────

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

function MiniCatTree({ tree, selected, primaryId, onToggle, onSetPrimary }: {
  tree: TreeNode[];
  selected: WooCatEntry[];
  primaryId: number | null;
  onToggle: (opt: WooOpt) => void;
  onSetPrimary: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const selIds = useMemo(() => new Set(selected.map(c => c.id)), [selected]);

  // Auto-expand all parent nodes whenever the tree changes
  useEffect(() => {
    const ids = new Set<number>();
    function collect(nodes: TreeNode[]) {
      for (const n of nodes) {
        if (n.children.length > 0) { ids.add(n.opt.id); collect(n.children); }
      }
    }
    collect(tree);
    setExpanded(ids);
  }, [tree]);

  function renderNode(node: TreeNode): React.ReactNode {
    const checked = selIds.has(node.opt.id);
    const isPrimary = node.opt.id === primaryId;
    const hasKids = node.children.length > 0;
    const isOpen = expanded.has(node.opt.id);
    return (
      <div key={node.opt.id}>
        <div
          className="flex items-center gap-1.5 py-0.5 px-1 rounded hover:bg-secondary/40 group"
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
            <button onClick={() => onSetPrimary(node.opt.id)} className="text-[10px] text-blue-400/70 hover:text-emerald-400 px-1 shrink-0 transition-colors">Set primary</button>
          )}
          {checked && isPrimary && <span className="text-[10px] text-emerald-400 shrink-0 px-1">Primary</span>}
        </div>
        {hasKids && isOpen && <div>{node.children.map(renderNode)}</div>}
      </div>
    );
  }

  if (!tree.length) return <div className="p-3 text-xs text-muted-foreground italic">No WooCommerce categories — sync from Stores page first.</div>;
  return (
    <div className="max-h-48 overflow-y-auto bg-black/20 rounded-lg border border-border/30 p-1">
      {tree.map(renderNode)}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Category Mapping Dictionary tab
// ─────────────────────────────────────────────────────────────────────────────

interface CatMapping {
  id: number;
  sunsky_cat: string;
  woo_cats: WooCatEntry[];
  primary_woo_cat_id: number | null;
  times_used: number;
  last_used_at: string | null;
}

function CategoryMappingDictionary() {
  const { toast } = useToast();
  const [stores, setStores] = useState<any[]>([]);
  const [storeId, setStoreId] = useState<number | null>(null);
  const [mappings, setMappings] = useState<CatMapping[]>([]);
  const [wooOpts, setWooOpts] = useState<WooOpt[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editSel, setEditSel] = useState<{ woo_cats: WooCatEntry[]; primary_id: number | null }>({ woo_cats: [], primary_id: null });
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [addingNew, setAddingNew] = useState(false);
  const [newSunskyCat, setNewSunskyCat] = useState("");
  const [newSel, setNewSel] = useState<{ woo_cats: WooCatEntry[]; primary_id: number | null }>({ woo_cats: [], primary_id: null });

  // Import Mapping state
  const importRef = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ imported: number; skipped: string[] } | null>(null);

  const wooTree = useMemo(() => buildTree(wooOpts), [wooOpts]);

  useEffect(() => {
    fetch("/api/stores")
      .then(r => r.json())
      .then(d => {
        const list = Array.isArray(d) ? d : (d.stores ?? []);
        setStores(list);
        if (list.length > 0) setStoreId(list[0].id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!storeId) return;
    setLoading(true);
    Promise.all([
      fetch(`/api/stores/${storeId}/category-mappings`).then(r => r.json()),
      fetch(`/api/stores/${storeId}/categories`).then(r => r.ok ? r.json() : []),
    ])
      .then(([mapData, catData]) => {
        setMappings(mapData.mappings ?? []);
        const cats = Array.isArray(catData) ? catData : (catData.categories ?? []);
        setWooOpts(cats.map((c: any) => ({
          id: c.wooId ?? c.woo_id ?? c.id,
          name: c.name,
          parent_id: c.parentId ?? c.parent_id ?? 0,
        })));
      })
      .catch(() => toast({ title: "Failed to load mappings", variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [storeId]);

  const reload = () => {
    if (!storeId) return;
    setLoading(true);
    fetch(`/api/stores/${storeId}/category-mappings`)
      .then(r => r.json())
      .then(d => setMappings(d.mappings ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  const handleImport = async (file: File) => {
    if (!storeId || !file) return;
    setImporting(true);
    setImportResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const r = await fetch(`/api/stores/${storeId}/category-mappings/import`, {
        method: "POST",
        body: formData,
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data?.detail || `Import failed (${r.status})`);
      setImportResult({ imported: data.imported, skipped: data.skipped ?? [] });
      toast({
        title: "Import complete",
        description: `${data.imported} mapping${data.imported !== 1 ? "s" : ""} imported${data.skipped?.length ? `, ${data.skipped.length} rows skipped` : ""}`,
      });
      reload();
    } catch (e: any) {
      toast({ title: "Import failed", description: e.message, variant: "destructive" });
    } finally {
      setImporting(false);
      if (importRef.current) importRef.current.value = "";
    }
  };

  const startEdit = (m: CatMapping) => {
    setEditingId(m.id);
    setEditSel({ woo_cats: m.woo_cats, primary_id: m.primary_woo_cat_id ?? m.woo_cats[0]?.id ?? null });
  };

  const toggleWoo = (opt: WooOpt) => {
    setEditSel(prev => {
      const has = prev.woo_cats.some(c => c.id === opt.id);
      const woo_cats = has ? prev.woo_cats.filter(c => c.id !== opt.id) : [...prev.woo_cats, { id: opt.id, name: opt.name }];
      const primary_id = has && prev.primary_id === opt.id ? (woo_cats[0]?.id ?? null) : (prev.primary_id ?? (!has ? opt.id : null));
      return { woo_cats, primary_id };
    });
  };

  const toggleNewWoo = (opt: WooOpt) => {
    setNewSel(prev => {
      const has = prev.woo_cats.some(c => c.id === opt.id);
      const woo_cats = has ? prev.woo_cats.filter(c => c.id !== opt.id) : [...prev.woo_cats, { id: opt.id, name: opt.name }];
      const primary_id = has && prev.primary_id === opt.id ? (woo_cats[0]?.id ?? null) : (prev.primary_id ?? (!has ? opt.id : null));
      return { woo_cats, primary_id };
    });
  };

  const handleSaveNew = async () => {
    if (!storeId || !newSunskyCat.trim()) return;
    setSaving(true);
    try {
      const primary_id = newSel.primary_id ?? (newSel.woo_cats[0]?.id ?? null);
      const r = await fetch(`/api/stores/${storeId}/category-mappings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify([{
          sunsky_cat: newSunskyCat.trim(),
          woo_cats: newSel.woo_cats,
          primary_woo_cat_id: primary_id,
        }]),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Mapping added" });
      setAddingNew(false);
      setNewSunskyCat("");
      setNewSel({ woo_cats: [], primary_id: null });
      reload();
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleSaveEdit = async (m: CatMapping) => {
    if (!storeId) return;
    setSaving(true);
    try {
      const r = await fetch(`/api/stores/${storeId}/category-mappings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify([{
          sunsky_cat: m.sunsky_cat,
          woo_cats: editSel.woo_cats,
          primary_woo_cat_id: editSel.primary_id,
        }]),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Mapping saved" });
      setEditingId(null);
      reload();
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (m: CatMapping) => {
    if (!storeId) return;
    setDeleting(m.id);
    try {
      const r = await fetch(`/api/stores/${storeId}/category-mappings/${m.id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Mapping deleted" });
      setMappings(prev => prev.filter(x => x.id !== m.id));
    } catch (e: any) {
      toast({ title: "Delete failed", description: e.message, variant: "destructive" });
    } finally {
      setDeleting(null);
    }
  };

  const filtered = useMemo(
    () => !search.trim() ? mappings : mappings.filter(m => m.sunsky_cat.toLowerCase().includes(search.toLowerCase())),
    [mappings, search]
  );

  if (stores.length === 0 && !loading) return (
    <div className="bg-card border border-border/50 rounded-2xl p-8 text-center text-muted-foreground text-sm">
      No stores configured. Add a store first on the Stores page.
    </div>
  );

  return (
    <div className="space-y-4">
      {/* Hidden file input for import */}
      <input
        ref={importRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        className="hidden"
        onChange={e => { const f = e.target.files?.[0]; if (f) handleImport(f); }}
      />

      {/* Store selector + search + actions */}
      <div className="flex flex-col sm:flex-row gap-3">
        <select
          value={storeId ?? ""}
          onChange={e => setStoreId(Number(e.target.value))}
          className="bg-background border border-border rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-primary w-full sm:w-56 shrink-0"
        >
          {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search Sunsky categories…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-background border border-border rounded-xl pl-10 pr-4 py-2 text-sm focus:outline-none focus:border-primary"
          />
        </div>
        <button onClick={reload} disabled={loading} className="p-2 rounded-xl border border-border hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors" title="Refresh">
          <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
        </button>
        <button
          onClick={() => importRef.current?.click()}
          disabled={importing || !storeId}
          title="Import mappings from Excel or CSV"
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-border bg-secondary/50 hover:bg-secondary text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-50 shrink-0 transition-colors"
        >
          {importing ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileSpreadsheet className="w-4 h-4" />}
          Import
        </button>
        <button
          onClick={() => { setAddingNew(true); setNewSunskyCat(""); setNewSel({ woo_cats: [], primary_id: null }); }}
          disabled={addingNew}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 shrink-0"
        >
          <Plus className="w-4 h-4" /> Add Mapping
        </button>
      </div>

      {/* Import result banner */}
      {importResult && (
        <div className="rounded-xl border border-border/60 bg-secondary/30 p-3 space-y-1.5">
          <div className="flex items-center gap-2 text-sm">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            <span className="font-medium text-emerald-400">{importResult.imported} mapping{importResult.imported !== 1 ? "s" : ""} imported</span>
            {importResult.skipped.length > 0 && (
              <span className="text-amber-400 text-xs">· {importResult.skipped.length} rows skipped</span>
            )}
            <button onClick={() => setImportResult(null)} className="ml-auto text-muted-foreground hover:text-foreground"><X className="w-3.5 h-3.5" /></button>
          </div>
          {importResult.skipped.length > 0 && (
            <ul className="text-xs text-muted-foreground pl-6 space-y-0.5 max-h-28 overflow-y-auto">
              {importResult.skipped.map((s, i) => (
                <li key={i} className="flex items-start gap-1"><AlertCircle className="w-3 h-3 text-amber-400 shrink-0 mt-0.5" />{s}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Inline "Add new mapping" form */}
      {addingNew && (
        <div className="rounded-2xl border border-primary/30 bg-primary/5 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-foreground">New Category Mapping</span>
            <button onClick={() => setAddingNew(false)} className="text-muted-foreground hover:text-foreground"><X className="w-4 h-4" /></button>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">Sunsky Category (ID or Name)</label>
            <input
              type="text"
              placeholder="e.g. 110358  or  Mobile Accessories"
              value={newSunskyCat}
              onChange={e => setNewSunskyCat(e.target.value)}
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-primary placeholder:font-sans placeholder:text-muted-foreground"
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">WooCommerce Categories</label>
            {newSel.woo_cats.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {newSel.woo_cats.map(c => (
                  <span
                    key={c.id}
                    onClick={() => c.id !== newSel.primary_id && setNewSel(prev => ({ ...prev, primary_id: c.id }))}
                    className={cn(
                      "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border cursor-pointer",
                      c.id === newSel.primary_id
                        ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                        : "bg-blue-500/15 text-blue-400 border-blue-500/30"
                    )}
                  >
                    {c.id === newSel.primary_id && <span className="text-[10px]">★</span>}
                    {c.name}
                    <button onClick={e => { e.stopPropagation(); toggleNewWoo({ id: c.id, name: c.name, parent_id: 0 }); }} className="ml-0.5 hover:text-red-400">×</button>
                  </span>
                ))}
              </div>
            )}
            <MiniCatTree
              tree={wooTree}
              selected={newSel.woo_cats}
              primaryId={newSel.primary_id}
              onToggle={toggleNewWoo}
              onSetPrimary={id => setNewSel(prev => ({ ...prev, primary_id: id }))}
            />
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleSaveNew}
              disabled={saving || !newSunskyCat.trim()}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
              Save Mapping
            </button>
            <button onClick={() => setAddingNew(false)} className="px-4 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Stats row */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span><span className="font-medium text-foreground">{filtered.length}</span> {search ? "matching" : "total"} {filtered.length === 1 ? "rule" : "rules"}</span>
        {mappings.length > 0 && (
          <span>
            <span className="font-medium text-foreground">{mappings.reduce((s, m) => s + (m.times_used || 0), 0)}</span> total uses
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-12 justify-center text-muted-foreground text-sm">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading mappings…
        </div>
      ) : filtered.length === 0 ? (
        <div className="bg-card border border-border/50 rounded-2xl p-8 text-center text-muted-foreground text-sm">
          {search ? "No mappings match your search." : "No saved mappings yet. Run a pipeline to build up the dictionary."}
        </div>
      ) : (
        <div className="bg-card border border-border/50 rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-secondary/40 border-b border-border/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Sunsky Category</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">WooCommerce Categories</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground w-20">Used</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground w-28">Last Used</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground w-24">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {filtered.map(m => (
                <tr key={m.id} className="hover:bg-secondary/10 transition-colors">
                  {editingId === m.id ? (
                    <td colSpan={5} className="p-4">
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-sm text-foreground">{m.sunsky_cat}</span>
                          <button onClick={() => setEditingId(null)} className="text-muted-foreground hover:text-foreground">
                            <X className="w-4 h-4" />
                          </button>
                        </div>

                        {/* Selected chips */}
                        {editSel.woo_cats.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {editSel.woo_cats.map(c => (
                              <span
                                key={c.id}
                                onClick={() => c.id !== editSel.primary_id && setEditSel(prev => ({ ...prev, primary_id: c.id }))}
                                className={cn(
                                  "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border cursor-pointer",
                                  c.id === editSel.primary_id
                                    ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                                    : "bg-blue-500/15 text-blue-400 border-blue-500/30"
                                )}
                              >
                                {c.id === editSel.primary_id && <span className="text-[10px]">★</span>}
                                {c.name}
                                <button onClick={e => { e.stopPropagation(); toggleWoo({ id: c.id, name: c.name, parent_id: 0 }); }} className="ml-0.5 hover:text-red-400">×</button>
                              </span>
                            ))}
                          </div>
                        )}

                        <MiniCatTree
                          tree={wooTree}
                          selected={editSel.woo_cats}
                          primaryId={editSel.primary_id}
                          onToggle={toggleWoo}
                          onSetPrimary={id => setEditSel(prev => ({ ...prev, primary_id: id }))}
                        />

                        <div className="flex gap-2">
                          <button
                            onClick={() => handleSaveEdit(m)}
                            disabled={saving}
                            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 disabled:opacity-50"
                          >
                            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                            Save
                          </button>
                          <button onClick={() => setEditingId(null)} className="px-4 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground">
                            Cancel
                          </button>
                        </div>
                      </div>
                    </td>
                  ) : (
                    <>
                      <td className="px-4 py-3 font-mono text-xs text-foreground">{m.sunsky_cat}</td>
                      <td className="px-4 py-3">
                        {m.woo_cats.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {m.woo_cats.map(c => (
                              <span key={c.id} className={cn(
                                "inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[11px] border",
                                c.id === (m.primary_woo_cat_id ?? m.woo_cats[0]?.id)
                                  ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/25"
                                  : "bg-blue-500/15 text-blue-400 border-blue-500/25"
                              )}>
                                {c.id === (m.primary_woo_cat_id ?? m.woo_cats[0]?.id) && <span className="text-[9px]">★</span>}
                                {c.name}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground/50 italic">No mapping</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">{m.times_used || 0}×</td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {m.last_used_at ? new Date(m.last_used_at).toLocaleDateString() : "—"}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => startEdit(m)}
                            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
                            title="Edit"
                          >
                            <Edit2 className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleDelete(m)}
                            disabled={deleting === m.id}
                            className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50"
                            title="Delete"
                          >
                            {deleting === m.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// AI Extraction Rules tab
// ─────────────────────────────────────────────────────────────────────────────

interface ExtractionRule {
  id: number;
  woo_attr_name: string;
  source_fields: string;
  instruction: string;
  confidence_threshold: number;
  if_not_found: string;
  default_value: string | null;
  sort_order: number;
}

const SOURCE_OPTS = [
  { value: "both",  label: "Title + Specs" },
  { value: "title", label: "Title only" },
  { value: "specs", label: "Specs only" },
];
const IF_NOT_FOUND_OPTS = [
  { value: "flag",        label: "Flag for review" },
  { value: "leave_blank", label: "Leave blank" },
  { value: "use_default", label: "Use default value" },
];

function AIExtractionRulesTab() {
  const { toast } = useToast();
  const [rules, setRules] = useState<ExtractionRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | "new" | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);

  const [stores, setStores] = useState<any[]>([]);
  const [storeId, setStoreId] = useState<number | null>(null);
  const [wooAttrs, setWooAttrs] = useState<any[]>([]);
  const [syncing, setSyncing] = useState(false);

  const emptyForm = (): Omit<ExtractionRule, "id"> => ({
    woo_attr_name: "",
    source_fields: "both",
    instruction: "",
    confidence_threshold: 0.7,
    if_not_found: "flag",
    default_value: null,
    sort_order: 0,
  });
  const [form, setForm] = useState(emptyForm());

  const load = () => {
    setLoading(true);
    fetch("/api/attr-rules")
      .then(r => r.json())
      .then(d => setRules(d.rules ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    fetch("/api/stores")
      .then(r => r.json())
      .then(d => {
        const list = Array.isArray(d) ? d : (d.stores ?? []);
        setStores(list);
        if (list.length > 0) setStoreId(list[0].id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!storeId) return;
    fetch(`/api/stores/${storeId}/woo-attributes`)
      .then(r => r.json())
      .then(d => setWooAttrs(Array.isArray(d) ? d : []))
      .catch(() => setWooAttrs([]));
  }, [storeId]);

  const syncAttrs = async () => {
    if (!storeId) return;
    setSyncing(true);
    try {
      const r = await fetch(`/api/stores/${storeId}/woo-attributes/sync`, { method: "POST" });
      if (!r.ok) throw new Error("Sync failed");
      const d = await r.json();
      toast({ title: `Synced ${d.synced_attributes ?? 0} attributes, ${d.synced_terms ?? 0} terms` });
      const r2 = await fetch(`/api/stores/${storeId}/woo-attributes`);
      const attrs = await r2.json();
      setWooAttrs(Array.isArray(attrs) ? attrs : []);
    } catch (e: any) {
      toast({ title: "Sync failed", description: e.message, variant: "destructive" });
    } finally {
      setSyncing(false);
    }
  };

  const startNew = () => { setForm(emptyForm()); setEditingId("new"); };
  const startEdit = (r: ExtractionRule) => {
    setForm({
      woo_attr_name: r.woo_attr_name,
      source_fields: r.source_fields,
      instruction: r.instruction,
      confidence_threshold: r.confidence_threshold,
      if_not_found: r.if_not_found,
      default_value: r.default_value,
      sort_order: r.sort_order,
    });
    setEditingId(r.id);
  };

  const handleSave = async () => {
    if (!form.woo_attr_name.trim()) return;
    setSaving(true);
    try {
      const isNew = editingId === "new";
      const url = isNew ? "/api/attr-rules" : `/api/attr-rules/${editingId}`;
      const r = await fetch(url, {
        method: isNew ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || "Save failed"); }
      toast({ title: isNew ? "Rule created" : "Rule updated" });
      setEditingId(null);
      load();
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    setDeleting(id);
    try {
      await fetch(`/api/attr-rules/${id}`, { method: "DELETE" });
      toast({ title: "Rule deleted" });
      setRules(prev => prev.filter(r => r.id !== id));
    } catch {
      toast({ title: "Delete failed", variant: "destructive" });
    } finally {
      setDeleting(null);
    }
  };

  const usedAttrNames = new Set(
    rules
      .filter(r => editingId === null || (typeof editingId === "number" && r.id !== editingId))
      .map(r => r.woo_attr_name.toLowerCase())
  );
  const availableWooAttrs = wooAttrs.filter(a => !usedAttrNames.has(a.name.toLowerCase()));

  const RuleForm = () => (
    <div className="rounded-2xl border border-primary/30 bg-primary/5 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-foreground">
          {editingId === "new" ? "New Extraction Rule" : "Edit Rule"}
        </span>
        <button onClick={() => setEditingId(null)} className="text-muted-foreground hover:text-foreground">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2 space-y-1">
          <label className="text-xs font-medium text-muted-foreground">WooCommerce Attribute Name *</label>
          {wooAttrs.length > 0 ? (
            <select
              value={form.woo_attr_name}
              onChange={e => setForm(f => ({ ...f, woo_attr_name: e.target.value }))}
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary"
            >
              <option value="">— select a WooCommerce attribute —</option>
              {availableWooAttrs.map(a => (
                <option key={a.id} value={a.name}>{a.name}</option>
              ))}
              {form.woo_attr_name && !availableWooAttrs.find((a: any) => a.name === form.woo_attr_name) && (
                <option value={form.woo_attr_name}>{form.woo_attr_name}</option>
              )}
            </select>
          ) : (
            <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-400">
              <Info className="w-3.5 h-3.5 shrink-0" />
              No WooCommerce attributes synced yet — click <strong>Sync Attributes</strong> above.
            </div>
          )}
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">Source Fields</label>
          <select
            value={form.source_fields}
            onChange={e => setForm(f => ({ ...f, source_fields: e.target.value }))}
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary"
          >
            {SOURCE_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">If Not Found</label>
          <select
            value={form.if_not_found}
            onChange={e => setForm(f => ({ ...f, if_not_found: e.target.value }))}
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary"
          >
            {IF_NOT_FOUND_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <div className="col-span-2 space-y-1">
          <label className="text-xs font-medium text-muted-foreground">AI Instruction</label>
          <textarea
            rows={2}
            placeholder="Natural-language guidance for the AI, e.g. 'Extract the primary color. Ignore background colors.'"
            value={form.instruction}
            onChange={e => setForm(f => ({ ...f, instruction: e.target.value }))}
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary resize-none placeholder:text-muted-foreground"
          />
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-muted-foreground">Confidence Threshold</label>
            <span className="text-sm font-bold text-foreground tabular-nums">
              {Math.round(form.confidence_threshold * 100)}%
            </span>
          </div>
          <input
            type="range"
            min={0} max={1} step={0.05}
            value={form.confidence_threshold}
            onChange={e => setForm(f => ({ ...f, confidence_threshold: parseFloat(e.target.value) }))}
            className="w-full accent-primary"
          />
          <div className="flex justify-between text-[10px] text-muted-foreground">
            <span>0%</span><span>50%</span><span>100%</span>
          </div>
        </div>

        {form.if_not_found === "use_default" && (
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">Default Value</label>
            <input
              type="text"
              placeholder="Fallback value when AI can't find it"
              value={form.default_value ?? ""}
              onChange={e => setForm(f => ({ ...f, default_value: e.target.value || null }))}
              className={inputCls}
            />
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving || !form.woo_attr_name.trim()}
          className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
          Save Rule
        </button>
        <button onClick={() => setEditingId(null)} className="px-4 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground">
          Cancel
        </button>
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-violet-400" />
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">AI Extraction Rules</h2>
        </div>
        <div className="flex items-center gap-2">
          {stores.length > 1 && (
            <select
              value={storeId ?? ""}
              onChange={e => setStoreId(Number(e.target.value))}
              className="bg-background border border-border rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-primary"
            >
              {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          )}
          {storeId && (
            <button
              onClick={syncAttrs}
              disabled={syncing}
              title="Sync WooCommerce attributes"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-secondary hover:bg-secondary/80 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={cn("w-3.5 h-3.5", syncing && "animate-spin")} />
              Sync Attributes
            </button>
          )}
          <button onClick={load} disabled={loading} className="p-2 rounded-xl border border-border hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors">
            <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
          </button>
          <button
            onClick={startNew}
            disabled={editingId !== null}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            <Plus className="w-4 h-4" /> Add Rule
          </button>
        </div>
      </div>

      <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-secondary/40 border border-border/40 text-xs text-muted-foreground">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Each rule controls how the AI extracts one WooCommerce attribute. Rules are applied during the <strong className="text-foreground">Enrich</strong> step of every pipeline.{" "}
          Only attributes synced from WooCommerce appear in the dropdown — create the attribute in WooCommerce first, then click <strong className="text-foreground">Sync Attributes</strong>.
        </span>
      </div>

      {editingId === "new" && <RuleForm />}

      {loading ? (
        <div className="flex items-center gap-2 py-12 justify-center text-muted-foreground text-sm">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading rules…
        </div>
      ) : rules.length === 0 && editingId === null ? (
        <div className="bg-card border border-border/50 rounded-2xl p-8 text-center text-muted-foreground text-sm">
          No extraction rules yet — click <strong className="text-foreground">Add Rule</strong> to create your first.
        </div>
      ) : (
        <div className="bg-card border border-border/50 rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-secondary/40 border-b border-border/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Attribute</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Source</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">If Missing</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Threshold</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground w-24">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {rules.map(r => (
                <tr key={r.id} className="hover:bg-secondary/10 transition-colors">
                  {editingId === r.id ? (
                    <td colSpan={5} className="p-4"><RuleForm /></td>
                  ) : (
                    <>
                      <td className="px-4 py-3">
                        <div className="font-medium text-foreground text-sm">{r.woo_attr_name}</div>
                        {r.instruction && <div className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{r.instruction}</div>}
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {SOURCE_OPTS.find(o => o.value === r.source_fields)?.label ?? r.source_fields}
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {IF_NOT_FOUND_OPTS.find(o => o.value === r.if_not_found)?.label ?? r.if_not_found}
                        {r.if_not_found === "use_default" && r.default_value && (
                          <span className="ml-1 font-mono text-foreground">({r.default_value})</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">{(r.confidence_threshold * 100).toFixed(0)}%</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          <button onClick={() => startEdit(r)} className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors" title="Edit">
                            <Edit2 className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleDelete(r.id)}
                            disabled={deleting === r.id}
                            className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50"
                            title="Delete"
                          >
                            {deleting === r.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
// Attribute Profiles tab
// ─────────────────────────────────────────────────────────────────────────────

interface ProfileAttr { woo_attr_name: string; required: boolean; sort_order: number; }
interface AttrProfile { id: number; name: string; description: string | null; attributes: ProfileAttr[]; }

function AttributeProfilesTab() {
  const { toast } = useToast();
  const [profiles, setProfiles] = useState<AttrProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | "new" | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const [stores, setStores] = useState<any[]>([]);
  const [storeId, setStoreId] = useState<number | null>(null);
  const [wooAttrs, setWooAttrs] = useState<any[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [selectedAttr, setSelectedAttr] = useState("");

  const emptyForm = () => ({ name: "", description: "", attributes: [] as ProfileAttr[] });
  const [form, setForm] = useState(emptyForm());

  const load = () => {
    setLoading(true);
    fetch("/api/attr-profiles")
      .then(r => r.json())
      .then(d => setProfiles(d.profiles ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    fetch("/api/stores")
      .then(r => r.json())
      .then(d => {
        const list = Array.isArray(d) ? d : (d.stores ?? []);
        setStores(list);
        if (list.length > 0) setStoreId(list[0].id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!storeId) return;
    fetch(`/api/stores/${storeId}/woo-attributes`)
      .then(r => r.json())
      .then(d => setWooAttrs(Array.isArray(d) ? d : []))
      .catch(() => setWooAttrs([]));
  }, [storeId]);

  const syncAttrs = async () => {
    if (!storeId) return;
    setSyncing(true);
    try {
      const r = await fetch(`/api/stores/${storeId}/woo-attributes/sync`, { method: "POST" });
      if (!r.ok) throw new Error("Sync failed");
      const d = await r.json();
      toast({ title: `Synced ${d.synced_attributes ?? 0} attributes, ${d.synced_terms ?? 0} terms` });
      const r2 = await fetch(`/api/stores/${storeId}/woo-attributes`);
      const attrs = await r2.json();
      setWooAttrs(Array.isArray(attrs) ? attrs : []);
    } catch (e: any) {
      toast({ title: "Sync failed", description: e.message, variant: "destructive" });
    } finally {
      setSyncing(false);
    }
  };

  const startNew = () => { setForm(emptyForm()); setSelectedAttr(""); setEditingId("new"); };
  const startEdit = (p: AttrProfile) => {
    setForm({ name: p.name, description: p.description ?? "", attributes: p.attributes.map(a => ({ ...a })) });
    setSelectedAttr("");
    setEditingId(p.id);
  };

  const addAttr = () => {
    if (!selectedAttr || form.attributes.some(a => a.woo_attr_name.toLowerCase() === selectedAttr.toLowerCase())) return;
    setForm(f => ({
      ...f,
      attributes: [...f.attributes, { woo_attr_name: selectedAttr, required: true, sort_order: f.attributes.length }],
    }));
    setSelectedAttr("");
  };

  const removeAttr = (name: string) => setForm(f => ({ ...f, attributes: f.attributes.filter(a => a.woo_attr_name !== name) }));

  const handleSave = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      const isNew = editingId === "new";
      const url = isNew ? "/api/attr-profiles" : `/api/attr-profiles/${editingId}`;
      const r = await fetch(url, {
        method: isNew ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: form.name.trim(), description: form.description || null, attributes: form.attributes }),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || "Save failed"); }
      toast({ title: isNew ? "Profile created" : "Profile updated" });
      setEditingId(null);
      load();
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    setDeleting(id);
    try {
      await fetch(`/api/attr-profiles/${id}`, { method: "DELETE" });
      toast({ title: "Profile deleted" });
      setProfiles(prev => prev.filter(p => p.id !== id));
    } catch {
      toast({ title: "Delete failed", variant: "destructive" });
    } finally {
      setDeleting(null);
    }
  };

  const ProfileForm = () => (
    <div className="rounded-2xl border border-primary/30 bg-primary/5 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">{editingId === "new" ? "New Attribute Profile" : "Edit Profile"}</span>
        <button onClick={() => setEditingId(null)} className="text-muted-foreground hover:text-foreground"><X className="w-4 h-4" /></button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">Profile Name *</label>
          <input type="text" placeholder="e.g. Electronics, Clothing" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} className={inputCls} />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">Description</label>
          <input type="text" placeholder="Optional note" value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} className={inputCls} />
        </div>
      </div>

      <div className="space-y-2">
        <label className="text-xs font-medium text-muted-foreground">
          Attributes — selected from WooCommerce
        </label>
        {form.attributes.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {form.attributes.map(a => (
              <span
                key={a.woo_attr_name}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-blue-500/15 text-blue-400 border border-blue-500/30 text-sm font-medium"
              >
                {a.woo_attr_name}
                <button
                  onClick={() => removeAttr(a.woo_attr_name)}
                  className="text-blue-400/60 hover:text-blue-400 ml-0.5"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          {wooAttrs.length > 0 ? (
            <select
              value={selectedAttr}
              onChange={e => setSelectedAttr(e.target.value)}
              className="flex-1 bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary"
            >
              <option value="">— select a WooCommerce attribute to add —</option>
              {wooAttrs
                .filter(a => !form.attributes.some(fa => fa.woo_attr_name.toLowerCase() === a.name.toLowerCase()))
                .map((a: any) => (
                  <option key={a.id} value={a.name}>{a.name}</option>
                ))
              }
            </select>
          ) : (
            <div className="flex-1 flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-400">
              <Info className="w-3.5 h-3.5 shrink-0" />
              No WooCommerce attributes synced yet — click <strong>Sync Attributes</strong> above.
            </div>
          )}
          <button
            onClick={addAttr}
            disabled={!selectedAttr}
            className="flex items-center gap-1 px-3 py-2 rounded-lg border border-border bg-secondary hover:bg-secondary/80 text-sm disabled:opacity-50"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Only attributes that exist in WooCommerce appear here. Create the attribute in WooCommerce first, then click <strong className="text-foreground">Sync Attributes</strong>.
        </p>
      </div>

      <div className="flex gap-2">
        <button onClick={handleSave} disabled={saving || !form.name.trim()} className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 disabled:opacity-50">
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
          Save Profile
        </button>
        <button onClick={() => setEditingId(null)} className="px-4 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground">Cancel</button>
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Tag className="w-4 h-4 text-blue-400" />
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Attribute Profiles</h2>
        </div>
        <div className="flex items-center gap-2">
          {stores.length > 1 && (
            <select
              value={storeId ?? ""}
              onChange={e => setStoreId(Number(e.target.value))}
              className="bg-background border border-border rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-primary"
            >
              {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          )}
          {storeId && (
            <button
              onClick={syncAttrs}
              disabled={syncing}
              title="Sync WooCommerce attributes"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-secondary hover:bg-secondary/80 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={cn("w-3.5 h-3.5", syncing && "animate-spin")} />
              Sync Attributes
            </button>
          )}
          <button onClick={load} disabled={loading} className="p-2 rounded-xl border border-border hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors">
            <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
          </button>
          <button onClick={startNew} disabled={editingId !== null} className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50">
            <Plus className="w-4 h-4" /> New Profile
          </button>
        </div>
      </div>

      <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-secondary/40 border border-border/40 text-xs text-muted-foreground">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Profiles define the expected WooCommerce attributes for a product category. Assign a profile to a Sunsky category mapping in the <strong className="text-foreground">Map step</strong> of a pipeline — the AI will extract those attributes for every product in that category.{" "}
          Only attributes synced from WooCommerce appear in the dropdown — click <strong className="text-foreground">Sync Attributes</strong> to pull from your store.
        </span>
      </div>

      {editingId === "new" && <ProfileForm />}

      {loading ? (
        <div className="flex items-center gap-2 py-12 justify-center text-muted-foreground text-sm">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading profiles…
        </div>
      ) : profiles.length === 0 && editingId === null ? (
        <div className="bg-card border border-border/50 rounded-2xl p-8 text-center text-muted-foreground text-sm">
          No profiles yet — click <strong className="text-foreground">New Profile</strong> to create your first.
        </div>
      ) : (
        <div className="space-y-2">
          {profiles.map(p => (
            <div key={p.id}>
              {editingId === p.id ? (
                <ProfileForm />
              ) : (
                <div className="bg-card border border-border/50 rounded-2xl overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-3 border-b border-border/40">
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      <button
                        onClick={() => setExpandedId(expandedId === p.id ? null : p.id)}
                        className="text-muted-foreground hover:text-foreground shrink-0"
                      >
                        {expandedId === p.id ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                      </button>
                      <div className="min-w-0">
                        <span className="font-semibold text-sm text-foreground">{p.name}</span>
                        {p.description && <span className="ml-2 text-xs text-muted-foreground">{p.description}</span>}
                      </div>
                      <span className="ml-auto text-xs text-muted-foreground shrink-0">{p.attributes.length} attr{p.attributes.length !== 1 ? "s" : ""}</span>
                    </div>
                    <div className="flex items-center gap-2 ml-4 shrink-0">
                      <button onClick={() => startEdit(p)} className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors" title="Edit">
                        <Edit2 className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleDelete(p.id)} disabled={deleting === p.id} className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50" title="Delete">
                        {deleting === p.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>
                  {expandedId === p.id && (
                    <div className="px-4 py-3 flex flex-wrap gap-1.5">
                      {p.attributes.length === 0 ? (
                        <span className="text-xs text-muted-foreground italic">No attributes</span>
                      ) : p.attributes.map(a => (
                        <span key={a.woo_attr_name} className={cn(
                          "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border",
                          a.required
                            ? "bg-amber-500/10 text-amber-400 border-amber-500/25"
                            : "bg-secondary text-muted-foreground border-border/50"
                        )}>
                          {a.woo_attr_name}
                          {!a.required && <span className="opacity-60 text-[10px]">opt</span>}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
// Inventory Mapping tab
// ─────────────────────────────────────────────────────────────────────────────

const NULL_OPTS = [
  { value: "leave_blank",  label: "Leave blank" },
  { value: "use_default",  label: "Use default value" },
  { value: "skip",         label: "Skip product" },
];

function InventoryMappingTab() {
  const { toast } = useToast();
  const [stores, setStores] = useState<any[]>([]);
  const [storeId, setStoreId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [cfg, setCfg] = useState<any>(null);

  useEffect(() => {
    fetch("/api/stores")
      .then(r => r.json())
      .then(d => {
        const list = Array.isArray(d) ? d : (d.stores ?? []);
        setStores(list);
        if (list.length > 0) setStoreId(list[0].id);
      });
  }, []);

  useEffect(() => {
    if (!storeId) return;
    setLoading(true);
    fetch(`/api/stores/${storeId}/inventory-mapping`)
      .then(r => r.json())
      .then(setCfg)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [storeId]);

  const update = (key: string, val: string | null) => setCfg((c: any) => ({ ...c, [key]: val }));

  const handleSave = async () => {
    if (!storeId || !cfg) return;
    setSaving(true);
    try {
      const r = await fetch(`/api/stores/${storeId}/inventory-mapping`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      if (!r.ok) throw new Error(await r.text());
      toast({ title: "Inventory mapping saved" });
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const Field = ({ label, fieldKey }: { label: string; fieldKey: string }) => {
    const nullKey = `${fieldKey}_null`;
    const defKey = `${fieldKey}_default`;
    return (
      <div className="rounded-xl border border-border/50 bg-secondary/20 p-4 space-y-3">
        <span className="text-sm font-semibold text-foreground">{label}</span>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">If missing in Sunsky</label>
            <select
              value={cfg?.[nullKey] ?? "leave_blank"}
              onChange={e => update(nullKey, e.target.value)}
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary"
            >
              {NULL_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          {cfg?.[nullKey] === "use_default" && (
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Default value</label>
              <input
                type="text"
                placeholder="e.g. 0.5"
                value={cfg?.[defKey] ?? ""}
                onChange={e => update(defKey, e.target.value || null)}
                className={inputCls}
              />
            </div>
          )}
        </div>
      </div>
    );
  };

  if (stores.length === 0) return (
    <div className="bg-card border border-border/50 rounded-2xl p-8 text-center text-muted-foreground text-sm">
      No stores configured. Add a store first on the Stores page.
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Tag className="w-4 h-4 text-emerald-400" />
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Inventory Mapping</h2>
      </div>

      <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-secondary/40 border border-border/40 text-xs text-muted-foreground">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Controls how Sunsky weight and dimension values are mapped to WooCommerce shipping fields during the <strong className="text-foreground">Upload</strong> step.
        </span>
      </div>

      <select
        value={storeId ?? ""}
        onChange={e => setStoreId(Number(e.target.value))}
        className="bg-background border border-border rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-primary w-full sm:w-64"
      >
        {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>

      {loading ? (
        <div className="flex items-center gap-2 py-12 justify-center text-muted-foreground text-sm">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </div>
      ) : cfg && (
        <div className="space-y-4">
          {/* Units */}
          <div className="rounded-xl border border-border/50 bg-secondary/20 p-4 space-y-3">
            <span className="text-sm font-semibold text-foreground">Units</span>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">Weight unit</label>
                <select value={cfg.weight_unit ?? "kg"} onChange={e => update("weight_unit", e.target.value)} className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary">
                  <option value="kg">kg</option>
                  <option value="g">g</option>
                  <option value="lbs">lbs</option>
                  <option value="oz">oz</option>
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">Dimension unit</label>
                <select value={cfg.dimension_unit ?? "cm"} onChange={e => update("dimension_unit", e.target.value)} className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary">
                  <option value="cm">cm</option>
                  <option value="m">m</option>
                  <option value="mm">mm</option>
                  <option value="in">in</option>
                  <option value="yd">yd</option>
                </select>
              </div>
            </div>
          </div>

          {/* Per-field null handling */}
          <Field label="Weight"        fieldKey="weight" />
          <Field label="Length"        fieldKey="length" />
          <Field label="Width"         fieldKey="width" />
          <Field label="Height"        fieldKey="height" />

          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 px-5 py-2 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save Configuration
          </button>
        </div>
      )}
    </div>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
// Main Settings page
// ─────────────────────────────────────────────────────────────────────────────

export default function Settings() {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState<"keys" | "mappings" | "rules" | "profiles" | "inventory">("keys");

  const [statuses, setStatuses] = useState<Record<string, ProviderStatus>>({});
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadStatuses = () => {
    setLoading(true);
    fetch("/api/settings/api-keys")
      .then((r) => r.json())
      .then(setStatuses)
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadStatuses(); }, []);

  const handleSave = async (provider: string) => {
    const key = (keyInputs[provider] ?? "").trim();
    if (!key) return;
    setSaving(provider);
    try {
      const r = await fetch("/api/settings/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [provider]: key }),
      });
      if (!r.ok) throw new Error(await r.text());
      const updated = await fetch("/api/settings/api-keys").then((x) => x.json());
      setStatuses(updated);
      setKeyInputs((prev) => ({ ...prev, [provider]: "" }));
      toast({
        title: "API key saved",
        description: `${PROVIDERS[provider]?.label} key is now active for pipelines.`,
      });
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(null);
    }
  };

  const handleRemove = async (provider: string) => {
    setRemoving(provider);
    try {
      const r = await fetch(`/api/settings/api-keys/${provider}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      const updated = await fetch("/api/settings/api-keys").then((x) => x.json());
      setStatuses(updated);
      toast({ title: "Key removed", description: "The stored API key has been deleted." });
    } catch (e: any) {
      toast({ title: "Remove failed", description: e.message, variant: "destructive" });
    } finally {
      setRemoving(null);
    }
  };

  const configuredCount = Object.values(statuses).filter((s) => s.configured).length;

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
            <SettingsIcon className="w-7 h-7 text-primary" />
            Settings
          </h1>
          <p className="text-muted-foreground mt-1">
            Configure API keys and pipeline preferences.
          </p>
        </div>
        {activeTab === "keys" && (
          <button
            onClick={loadStatuses}
            disabled={loading}
            className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            title="Refresh status"
          >
            <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
          </button>
        )}
      </div>

      {/* Tab switcher */}
      <div className="flex flex-wrap gap-1 p-1 bg-secondary/40 rounded-xl w-fit border border-border/30">
        {(
          [
            { id: "keys",      icon: <Sparkles className="w-3.5 h-3.5" />, label: "AI Provider Keys" },
            { id: "mappings",  icon: <Tag className="w-3.5 h-3.5" />,      label: "Category Mappings" },
            { id: "rules",     icon: <Sparkles className="w-3.5 h-3.5" />, label: "Extraction Rules" },
            { id: "profiles",  icon: <Tag className="w-3.5 h-3.5" />,      label: "Attribute Profiles" },
            { id: "inventory", icon: <Tag className="w-3.5 h-3.5" />,      label: "Inventory Mapping" },
          ] as const
        ).map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-all",
              activeTab === tab.id
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "keys" ? (
        <>
          {/* AI Provider Keys */}
          <div className="bg-card border border-border/50 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-border/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-violet-400" />
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                  AI Provider Keys
                </h2>
              </div>
              <span className="text-xs text-muted-foreground">
                {configuredCount} / {Object.keys(PROVIDERS).length} configured
              </span>
            </div>

            <div className="mx-6 mt-4 mb-2 flex items-start gap-2 px-3 py-2.5 rounded-xl bg-secondary/40 border border-border/40 text-xs text-muted-foreground">
              <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                Keys are stored server-side and used by all pipelines.{" "}
                <strong className="text-foreground">Environment variable keys take priority</strong>{" "}
                (set via Replit Secrets) — those show as "Set via env" and cannot be overridden here.
              </span>
            </div>

            {loading ? (
              <div className="flex items-center gap-2 text-muted-foreground text-sm py-12 justify-center">
                <Loader2 className="w-4 h-4 animate-spin" /> Loading provider status…
              </div>
            ) : (
              <div className="px-6 pb-6 pt-2 space-y-4">
                {Object.entries(PROVIDERS).map(([id, info]) => {
                  const status = statuses[id];
                  const isConfigured = status?.configured ?? false;
                  const isEnvKey = status?.source === "env";
                  const currentInput = keyInputs[id] ?? "";

                  return (
                    <div key={id} className="rounded-xl border border-border/50 bg-secondary/20 overflow-hidden">
                      <div className="flex items-center justify-between px-4 py-3 border-b border-border/30">
                        <div className="flex items-center gap-3">
                          <Key className={cn("w-4 h-4", info.color)} />
                          <div>
                            <p className={cn("text-sm font-semibold", info.color)}>{info.label}</p>
                            <p className="text-xs text-muted-foreground">{info.description}</p>
                          </div>
                        </div>
                        {isConfigured ? (
                          <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border font-medium bg-emerald-500/10 text-emerald-400 border-emerald-500/20">
                            <CheckCircle2 className="w-3 h-3" />
                            {isEnvKey ? "Set via env" : "Active"}
                          </span>
                        ) : (
                          <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-secondary text-muted-foreground border border-border font-medium">
                            <XCircle className="w-3 h-3" />
                            Not set
                          </span>
                        )}
                      </div>

                      <div className="px-4 py-3 space-y-3">
                        {isEnvKey ? (
                          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-500/5 border border-emerald-500/15 text-xs text-muted-foreground">
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                            Key is provided via the{" "}
                            <code className="font-mono px-1 bg-secondary rounded">{info.envVar}</code>{" "}
                            environment variable — no action needed.
                          </div>
                        ) : (
                          <>
                            {isConfigured && status?.masked && (
                              <div className="flex items-center justify-between px-3 py-2 rounded-lg bg-secondary/60 border border-border/40">
                                <div className="flex items-center gap-2">
                                  <Key className="w-3 h-3 text-muted-foreground" />
                                  <span className="text-xs font-mono text-muted-foreground tracking-widest">
                                    {status.masked}
                                  </span>
                                </div>
                                <button
                                  onClick={() => handleRemove(id)}
                                  disabled={removing === id}
                                  className="flex items-center gap-1.5 text-xs text-red-400 hover:text-red-300 transition-colors disabled:opacity-50"
                                >
                                  {removing === id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                                  Remove
                                </button>
                              </div>
                            )}
                            <div className="flex gap-2">
                              <div className="relative flex-1">
                                <input
                                  type={visible[id] ? "text" : "password"}
                                  value={currentInput}
                                  onChange={(e) => setKeyInputs((prev) => ({ ...prev, [id]: e.target.value }))}
                                  placeholder={isConfigured ? "Paste new key to replace…" : `Paste your ${info.envVar} here`}
                                  className={cn(inputCls, "pr-10")}
                                  onKeyDown={(e) => { if (e.key === "Enter" && currentInput) handleSave(id); }}
                                />
                                <button
                                  type="button"
                                  onClick={() => setVisible((v) => ({ ...v, [id]: !v[id] }))}
                                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                                >
                                  {visible[id] ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                                </button>
                              </div>
                              <button
                                onClick={() => handleSave(id)}
                                disabled={!currentInput.trim() || saving === id}
                                className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                              >
                                {saving === id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                                Save
                              </button>
                            </div>
                          </>
                        )}

                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs text-muted-foreground">Models:</span>
                          {info.models.map((m) => (
                            <span key={m} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground">
                              {m}
                            </span>
                          ))}
                        </div>

                        <a
                          href={info.docsUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline"
                        >
                          <ExternalLink className="w-3 h-3" />
                          Get your key at {info.docsLabel}
                        </a>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="flex items-start gap-2 px-4 py-3 rounded-xl bg-secondary/30 border border-border/40 text-xs text-muted-foreground">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              After saving a key, go to{" "}
              <strong className="text-foreground">Content Generation</strong> to enable AI mode on
              your fields and pick a model. Keys take effect immediately — no restart required.
            </span>
          </div>
        </>
      ) : activeTab === "mappings" ? (
        <>
          <div className="flex items-center gap-2 mb-1">
            <Tag className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Category Mapping Dictionary</h2>
          </div>
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-secondary/40 border border-border/40 text-xs text-muted-foreground mb-2">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              Saved rules auto-apply on future pipeline runs — no manual remapping needed once a category is in the dictionary.
              Click <strong className="text-foreground">Edit</strong> to change WooCommerce assignments, or <strong className="text-foreground">Delete</strong> to remove a rule entirely.
            </span>
          </div>
          <CategoryMappingDictionary />
        </>
      ) : activeTab === "rules" ? (
        <AIExtractionRulesTab />
      ) : activeTab === "profiles" ? (
        <AttributeProfilesTab />
      ) : (
        <InventoryMappingTab />
      )}
    </div>
  );
}
