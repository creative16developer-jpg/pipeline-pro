import { useEffect, useState, useCallback } from "react";
import { CloudDownload, Info, Search, ChevronRight } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

type Category = { id: string; name: string };

async function fetchLevel(parentId: string, signal?: AbortSignal): Promise<Category[]> {
  const res = await fetch(`/api/sunsky/categories?parent_id=${parentId}`, { signal });
  if (!res.ok) throw new Error(`Categories error (${res.status})`);
  const data = await res.json();
  const list: any[] = Array.isArray(data) ? data : [];
  return list
    .map((c: any) => ({ id: String(c.id ?? ""), name: String(c.name ?? "") }))
    .filter((c) => c.id && c.name)
    .sort((a, b) => a.name.localeCompare(b.name));
}

export default function Sunsky() {
  const { toast } = useToast();
  const [parentCats, setParentCats] = useState<Category[]>([]);
  const [childCats, setChildCats] = useState<Category[]>([]);
  const [parentLoading, setParentLoading] = useState(true);
  const [childLoading, setChildLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [parentId, setParentId] = useState("");
  const [childId, setChildId] = useState("");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(50);
  const [lastResult, setLastResult] = useState<any>(null);

  const effectiveCategoryId = childId || parentId;

  useEffect(() => {
    const controller = new AbortController();
    setParentLoading(true);
    fetchLevel("0", controller.signal)
      .then(setParentCats)
      .catch((err) => {
        if (err.name !== "AbortError") toast({ title: "Could not load categories", description: err.message, variant: "destructive" });
      })
      .finally(() => setParentLoading(false));
    return () => controller.abort();
  }, [toast]);

  const handleParentChange = useCallback((id: string) => {
    setParentId(id);
    setChildId("");
    setChildCats([]);
    if (!id) return;
    const controller = new AbortController();
    setChildLoading(true);
    fetchLevel(id, controller.signal)
      .then(setChildCats)
      .catch((err) => {
        if (err.name !== "AbortError") toast({ title: "Could not load sub-categories", description: err.message, variant: "destructive" });
      })
      .finally(() => setChildLoading(false));
  }, [toast]);

  const handleFetch = async (e: React.FormEvent) => {
    e.preventDefault();
    setFetching(true);
    try {
      const res = await fetch("/api/sunsky/fetch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category_id: effectiveCategoryId || undefined,
          keyword: keyword || undefined,
          page,
          limit,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || data?.message || `Fetch failed (${res.status})`);
      setLastResult(data);
      toast({ title: "Products fetched", description: `Using category ${effectiveCategoryId || "all"}` });
    } catch (err: any) {
      toast({ title: "Fetch Failed", description: err.message, variant: "destructive" });
    } finally {
      setFetching(false);
    }
  };

  const inputClass = "w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all";

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
          <CloudDownload className="w-8 h-8 text-primary" /> Sunsky Integration
        </h1>
        <p className="text-muted-foreground mt-2">Pull products directly from Sunsky's catalog into your pipeline.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
        <div className="md:col-span-2">
          <form onSubmit={handleFetch} className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5 space-y-6">
            <h2 className="text-xl font-display font-semibold border-b border-border/50 pb-2">Fetch Configuration</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Parent Category</label>
                <select value={parentId} onChange={(e) => handleParentChange(e.target.value)} className={inputClass} disabled={parentLoading}>
                  <option value="">{parentLoading ? "Loading…" : "— All Categories —"}</option>
                  {parentCats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground flex items-center gap-1">
                  <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                  Sub-Category
                  {childLoading && <span className="ml-2 w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin inline-block" />}
                </label>
                <select value={childId} onChange={(e) => setChildId(e.target.value)} className={inputClass} disabled={!parentId || childLoading || childCats.length === 0}>
                  <option value="">{!parentId ? "Select parent first" : childLoading ? "Loading…" : childCats.length === 0 ? "No sub-categories" : "— All in parent —"}</option>
                  {childCats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>

              <div className="space-y-2 sm:col-span-2">
                <label className="text-sm font-medium text-foreground">Keyword Filter</label>
                <div className="relative">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <input type="text" value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="e.g. iPhone Case" className="w-full bg-background border border-border rounded-xl pl-9 pr-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all" />
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">API Page</label>
                <input type="number" min="1" value={page} onChange={(e) => setPage(parseInt(e.target.value) || 1)} className={inputClass} />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Items per Page</label>
                <input type="number" min="1" max="100" value={limit} onChange={(e) => setLimit(parseInt(e.target.value) || 50)} className={inputClass} />
              </div>
            </div>

            <div className="text-xs text-muted-foreground">Selected category: <span className="font-mono text-primary">{effectiveCategoryId || "all"}</span></div>

            <div className="pt-2">
              <button type="submit" disabled={fetching} className="w-full py-4 rounded-xl bg-primary text-primary-foreground font-medium text-lg shadow-lg shadow-primary/25 hover:shadow-xl hover:shadow-primary/40 hover:-translate-y-0.5 transition-all disabled:opacity-50 disabled:transform-none flex items-center justify-center gap-3">
                {fetching ? <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" /> : <CloudDownload className="w-6 h-6" />}
                {fetching ? "Fetching Products…" : "Fetch Products"}
              </button>
            </div>
          </form>
        </div>

        <div className="space-y-6">
          <div className="bg-secondary/30 border border-border rounded-2xl p-5">
            <h3 className="font-medium flex items-center gap-2 mb-3"><Info className="w-4 h-4 text-primary" /> How it works</h3>
            <ol className="text-sm text-muted-foreground leading-relaxed space-y-2 list-decimal list-inside">
              <li>Pick a parent category</li>
              <li>Optionally pick a sub-category</li>
              <li>Fetch with page/limit</li>
            </ol>
          </div>

          {lastResult && (
            <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-lg shadow-black/5">
              <h3 className="font-display font-medium text-lg mb-4">Last Fetch Result</h3>
              <div className="space-y-3">
                <div className="flex justify-between items-center py-2 border-b border-border/50"><span className="text-muted-foreground text-sm">Fetched</span><span className="font-medium">{lastResult.fetched}</span></div>
                <div className="flex justify-between items-center py-2 border-b border-border/50"><span className="text-muted-foreground text-sm">Saved New</span><span className="font-medium text-emerald-400">{lastResult.saved}</span></div>
                <div className="flex justify-between items-center py-2 border-b border-border/50"><span className="text-muted-foreground text-sm">Skipped</span><span className="font-medium text-amber-400">{lastResult.skipped}</span></div>
                <div className="flex justify-between items-center py-2"><span className="text-muted-foreground text-sm">Job ID</span><span className="font-medium text-primary">#{lastResult.job_id ?? lastResult.jobId}</span></div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
