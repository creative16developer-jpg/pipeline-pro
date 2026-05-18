import { useEffect, useState, useCallback, useRef } from "react";
import { CloudDownload, Info, Search, ChevronRight, Upload, Trash2, FileText, CheckCircle2 } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

type Category = { id: string; name: string };
type Store = { id: number; name: string };

type CsvMapping = {
  id: number;
  sunsky_sku: string;
  site_sku: string | null;
  csv_title: string | null;
  created_at: string | null;
};

type CsvResult = {
  imported: number;
  errors: string[];
  preview: { sunsky_sku: string; site_sku: string; csv_title: string }[];
};

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
  const [stores, setStores] = useState<Store[]>([]);
  const [storeId, setStoreId] = useState<string>("");
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

  // CSV Upload state
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvUploading, setCsvUploading] = useState(false);
  const [csvResult, setCsvResult] = useState<CsvResult | null>(null);
  const [csvMappings, setCsvMappings] = useState<CsvMapping[]>([]);
  const [csvMappingsLoaded, setCsvMappingsLoaded] = useState(false);
  const [clearingCsv, setClearingCsv] = useState(false);
  const csvInputRef = useRef<HTMLInputElement>(null);

  const effectiveCategoryId = childId || parentId;

  useEffect(() => {
    fetch("/api/stores")
      .then((r) => r.json())
      .then((data) => {
        const list: Store[] = (Array.isArray(data) ? data : data?.stores ?? []).map((s: any) => ({
          id: s.id,
          name: s.name,
        }));
        setStores(list);
        if (list.length === 1) setStoreId(String(list[0].id));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setParentLoading(true);
    fetchLevel("0", controller.signal)
      .then(setParentCats)
      .catch((err) => {
        if (err.name !== "AbortError")
          toast({ title: "Could not load categories", description: err.message, variant: "destructive" });
      })
      .finally(() => setParentLoading(false));
    return () => controller.abort();
  }, [toast]);

  useEffect(() => {
    fetch("/api/csv/mappings")
      .then((r) => r.json())
      .then((data) => {
        setCsvMappings(data.mappings ?? []);
        setCsvMappingsLoaded(true);
      })
      .catch(() => setCsvMappingsLoaded(true));
  }, []);

  const handleParentChange = useCallback(
    (id: string) => {
      setParentId(id);
      setChildId("");
      setChildCats([]);
      if (!id) return;
      const controller = new AbortController();
      setChildLoading(true);
      fetchLevel(id, controller.signal)
        .then(setChildCats)
        .catch((err) => {
          if (err.name !== "AbortError")
            toast({ title: "Could not load sub-categories", description: err.message, variant: "destructive" });
        })
        .finally(() => setChildLoading(false));
    },
    [toast]
  );

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
          store_id: storeId ? parseInt(storeId) : undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || data?.message || `Fetch failed (${res.status})`);
      setLastResult(data);
      const storeName = stores.find((s) => String(s.id) === storeId)?.name;
      toast({
        title: "Products fetched",
        description: `${data.fetched} products${storeName ? ` for ${storeName}` : ""}`,
      });
    } catch (err: any) {
      toast({ title: "Fetch Failed", description: err.message, variant: "destructive" });
    } finally {
      setFetching(false);
    }
  };

  const handleCsvUpload = async () => {
    if (!csvFile) return;
    setCsvUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", csvFile);
      const res = await fetch("/api/csv/upload", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || `Upload failed (${res.status})`);
      setCsvResult(data);
      // Refresh mapping count
      const mapRes = await fetch("/api/csv/mappings");
      const mapData = await mapRes.json();
      setCsvMappings(mapData.mappings ?? []);
      toast({
        title: "CSV imported",
        description: `${data.imported} mappings loaded${data.errors?.length ? `, ${data.errors.length} rows skipped` : ""}`,
      });
    } catch (err: any) {
      toast({ title: "CSV Upload Failed", description: err.message, variant: "destructive" });
    } finally {
      setCsvUploading(false);
    }
  };

  const handleCsvClear = async () => {
    setClearingCsv(true);
    try {
      await fetch("/api/csv/mappings", { method: "DELETE" });
      setCsvMappings([]);
      setCsvResult(null);
      setCsvFile(null);
      if (csvInputRef.current) csvInputRef.current.value = "";
      toast({ title: "CSV mappings cleared" });
    } catch {
      toast({ title: "Could not clear mappings", variant: "destructive" });
    } finally {
      setClearingCsv(false);
    }
  };

  const inputClass =
    "w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all";

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
          <CloudDownload className="w-8 h-8 text-primary" /> Sunsky Integration
        </h1>
        <p className="text-muted-foreground mt-2">Pull products directly from Sunsky's catalog into your pipeline.</p>
      </div>

      {/* ── CSV Title Mapping ─────────────────────────────────────────────── */}
      <div className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-xl font-display font-semibold flex items-center gap-2">
              <FileText className="w-5 h-5 text-primary" /> CSV Title Mapping
            </h2>
            <p className="text-sm text-muted-foreground mt-0.5">
              Upload a CSV before fetching to override Sunsky titles and set your own SKUs.
            </p>
          </div>
          {csvMappings.length > 0 && (
            <div className="flex items-center gap-2 text-sm text-emerald-400">
              <CheckCircle2 className="w-4 h-4" />
              {csvMappings.length} mapping{csvMappings.length !== 1 ? "s" : ""} active
            </div>
          )}
        </div>

        {/* Format guide */}
        <div className="bg-secondary/30 rounded-xl p-4 mb-5 text-sm space-y-1">
          <p className="font-medium text-foreground mb-2">Required CSV columns (exact names):</p>
          <div className="grid grid-cols-3 gap-2">
            {[
              { col: "Sunsky SKU", desc: "Sunsky product SKU — used for matching" },
              { col: "Site SKU", desc: "Your internal SKU — saved to WooCommerce" },
              { col: "Product Title", desc: "Replaces Sunsky title in generation" },
            ].map(({ col, desc }) => (
              <div key={col} className="bg-background/60 rounded-lg p-3">
                <p className="font-mono text-xs text-primary font-semibold">{col}</p>
                <p className="text-xs text-muted-foreground mt-1">{desc}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="flex gap-3">
          <label className="flex-1 cursor-pointer">
            <input
              ref={csvInputRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setCsvFile(f);
                setCsvResult(null);
              }}
            />
            <div className="w-full h-12 rounded-xl border-2 border-dashed border-border hover:border-primary transition-colors flex items-center justify-center gap-2 text-sm text-muted-foreground hover:text-foreground">
              <Upload className="w-4 h-4" />
              {csvFile ? (
                <span className="text-foreground font-medium">{csvFile.name}</span>
              ) : (
                "Click to select CSV file"
              )}
            </div>
          </label>
          <button
            disabled={!csvFile || csvUploading}
            onClick={handleCsvUpload}
            className="px-5 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium shadow hover:shadow-lg hover:-translate-y-0.5 transition-all disabled:opacity-50 disabled:transform-none flex items-center gap-2 whitespace-nowrap"
          >
            {csvUploading ? (
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            {csvUploading ? "Uploading…" : "Upload"}
          </button>
          {csvMappings.length > 0 && (
            <button
              disabled={clearingCsv}
              onClick={handleCsvClear}
              className="px-4 py-2.5 rounded-xl border border-destructive/50 text-destructive hover:bg-destructive/10 transition-colors flex items-center gap-2 disabled:opacity-50"
            >
              <Trash2 className="w-4 h-4" />
              Clear
            </button>
          )}
        </div>

        {/* Import result */}
        {csvResult && (
          <div className="mt-4 space-y-3">
            <div className="flex items-center gap-3 text-sm">
              <span className="px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-400 font-medium">
                {csvResult.imported} imported
              </span>
              {csvResult.errors.length > 0 && (
                <span className="px-2.5 py-1 rounded-full bg-amber-500/20 text-amber-400 font-medium">
                  {csvResult.errors.length} skipped
                </span>
              )}
            </div>

            {csvResult.preview.length > 0 && (
              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border bg-secondary/30">
                      <th className="text-left px-3 py-2 text-muted-foreground font-medium">Sunsky SKU</th>
                      <th className="text-left px-3 py-2 text-muted-foreground font-medium">Site SKU</th>
                      <th className="text-left px-3 py-2 text-muted-foreground font-medium">CSV Title</th>
                    </tr>
                  </thead>
                  <tbody>
                    {csvResult.preview.map((row, i) => (
                      <tr key={i} className="border-b border-border/50 last:border-0">
                        <td className="px-3 py-2 font-mono text-primary">{row.sunsky_sku}</td>
                        <td className="px-3 py-2 font-mono text-muted-foreground">{row.site_sku || "—"}</td>
                        <td className="px-3 py-2 text-foreground truncate max-w-[200px]">{row.csv_title || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {csvResult.imported > csvResult.preview.length && (
                  <p className="text-xs text-muted-foreground px-3 py-2 border-t border-border/50">
                    …and {csvResult.imported - csvResult.preview.length} more rows
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* Active mappings count (when no recent upload result) */}
        {!csvResult && csvMappingsLoaded && csvMappings.length === 0 && (
          <p className="text-xs text-muted-foreground mt-3">
            No CSV mappings active. Upload a CSV to override Sunsky titles before the next pipeline.
          </p>
        )}
      </div>

      {/* ── Fetch Configuration ──────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
        <div className="md:col-span-2">
          <form onSubmit={handleFetch} className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5 space-y-6">
            <h2 className="text-xl font-display font-semibold border-b border-border/50 pb-2">Fetch Configuration</h2>

            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">
                Target Store <span className="text-muted-foreground font-normal">(optional — links this fetch job to a store)</span>
              </label>
              <select value={storeId} onChange={(e) => setStoreId(e.target.value)} className={inputClass}>
                <option value="">— No store (unlinked fetch) —</option>
                {stores.map((s) => (
                  <option key={s.id} value={String(s.id)}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Parent Category</label>
                <select
                  value={parentId}
                  onChange={(e) => handleParentChange(e.target.value)}
                  className={inputClass}
                  disabled={parentLoading}
                >
                  <option value="">{parentLoading ? "Loading…" : "— All Categories —"}</option>
                  {parentCats.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground flex items-center gap-1">
                  <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                  Sub-Category
                  {childLoading && (
                    <span className="ml-2 w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin inline-block" />
                  )}
                </label>
                <select
                  value={childId}
                  onChange={(e) => setChildId(e.target.value)}
                  className={inputClass}
                  disabled={!parentId || childLoading || childCats.length === 0}
                >
                  <option value="">
                    {!parentId
                      ? "Select parent first"
                      : childLoading
                      ? "Loading…"
                      : childCats.length === 0
                      ? "No sub-categories"
                      : "— All in parent —"}
                  </option>
                  {childCats.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2 sm:col-span-2">
                <label className="text-sm font-medium text-foreground">Keyword Filter</label>
                <div className="relative">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <input
                    type="text"
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="e.g. iPhone Case"
                    className="w-full bg-background border border-border rounded-xl pl-9 pr-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">API Page</label>
                <input
                  type="number"
                  min="1"
                  value={page}
                  onChange={(e) => setPage(parseInt(e.target.value) || 1)}
                  className={inputClass}
                />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Items per Page</label>
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={limit}
                  onChange={(e) => setLimit(parseInt(e.target.value) || 50)}
                  className={inputClass}
                />
              </div>
            </div>

            <div className="text-xs text-muted-foreground">
              Selected category:{" "}
              <span className="font-mono text-primary">{effectiveCategoryId || "all"}</span>
            </div>

            <div className="pt-2">
              <button
                type="submit"
                disabled={fetching}
                className="w-full py-4 rounded-xl bg-primary text-primary-foreground font-medium text-lg shadow-lg shadow-primary/25 hover:shadow-xl hover:shadow-primary/40 hover:-translate-y-0.5 transition-all disabled:opacity-50 disabled:transform-none flex items-center justify-center gap-3"
              >
                {fetching ? (
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <CloudDownload className="w-6 h-6" />
                )}
                {fetching ? "Fetching Products…" : "Fetch Products"}
              </button>
            </div>
          </form>
        </div>

        <div className="space-y-6">
          <div className="bg-secondary/30 border border-border rounded-2xl p-5">
            <h3 className="font-medium flex items-center gap-2 mb-3">
              <Info className="w-4 h-4 text-primary" /> How it works
            </h3>
            <ol className="text-sm text-muted-foreground leading-relaxed space-y-2 list-decimal list-inside">
              <li>Upload CSV mappings (optional)</li>
              <li>Optionally pick a target store</li>
              <li>Pick a parent category</li>
              <li>Optionally pick a sub-category</li>
              <li>Fetch with page/limit</li>
            </ol>
          </div>

          {lastResult && (
            <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-lg shadow-black/5">
              <h3 className="font-display font-medium text-lg mb-4">Last Fetch Result</h3>
              <div className="space-y-3">
                <div className="flex justify-between items-center py-2 border-b border-border/50">
                  <span className="text-muted-foreground text-sm">Fetched</span>
                  <span className="font-medium">{lastResult.fetched}</span>
                </div>
                <div className="flex justify-between items-center py-2 border-b border-border/50">
                  <span className="text-muted-foreground text-sm">Saved New</span>
                  <span className="font-medium text-emerald-400">{lastResult.saved}</span>
                </div>
                <div className="flex justify-between items-center py-2 border-b border-border/50">
                  <span className="text-muted-foreground text-sm">Skipped</span>
                  <span className="font-medium text-amber-400">{lastResult.skipped}</span>
                </div>
                <div className="flex justify-between items-center py-2">
                  <span className="text-muted-foreground text-sm">Job ID</span>
                  <span className="font-medium text-primary">#{lastResult.job_id ?? lastResult.jobId}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
