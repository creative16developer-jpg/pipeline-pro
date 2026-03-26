import { useState } from "react";
import { useCategories, useSunskyFetch } from "@/hooks/use-sunsky";
import { CloudDownload, AlertCircle, Info, Search } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

export default function Sunsky() {
  const { data: categories } = useCategories();
  const fetchMutation = useSunskyFetch();
  const { toast } = useToast();

  const [categoryId, setCategoryId] = useState("");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(50);
  const [lastResult, setLastResult] = useState<any>(null);

  const handleFetch = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const result = await fetchMutation.mutateAsync({
        data: {
          categoryId: categoryId || undefined,
          keyword: keyword || undefined,
          page,
          limit
        }
      });
      setLastResult(result);
      toast({ title: "Fetch Initiated", description: "Products are being fetched in the background." });
    } catch (e: any) {
      toast({ title: "Fetch Failed", description: e.message, variant: "destructive" });
    }
  };

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
            <div className="space-y-4">
              <h2 className="text-xl font-display font-semibold border-b border-border/50 pb-2">Fetch Configuration</h2>
              
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-foreground">Target Category</label>
                  <select
                    value={categoryId}
                    onChange={(e) => setCategoryId(e.target.value)}
                    className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all"
                  >
                    <option value="">All Categories</option>
                    {categories?.map(c => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                </div>
                
                <div className="space-y-2">
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
                    className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium text-foreground">Items per Page (Limit)</label>
                  <input
                    type="number"
                    min="1"
                    max="100"
                    value={limit}
                    onChange={(e) => setLimit(parseInt(e.target.value) || 50)}
                    className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all"
                  />
                </div>
              </div>
            </div>

            <div className="pt-2">
              <button
                type="submit"
                disabled={fetchMutation.isPending}
                className="w-full py-4 rounded-xl bg-primary text-primary-foreground font-medium text-lg shadow-lg shadow-primary/25 hover:shadow-xl hover:shadow-primary/40 hover:-translate-y-0.5 transition-all disabled:opacity-50 disabled:transform-none flex items-center justify-center gap-3"
              >
                {fetchMutation.isPending ? (
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <CloudDownload className="w-6 h-6" />
                )}
                {fetchMutation.isPending ? "Fetching Products..." : "Fetch Products"}
              </button>
            </div>
          </form>
        </div>

        <div className="space-y-6">
          <div className="bg-secondary/30 border border-border rounded-2xl p-5">
            <h3 className="font-medium flex items-center gap-2 mb-3">
              <Info className="w-4 h-4 text-primary" /> API Limits
            </h3>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Sunsky API imposes rate limits. It is recommended to fetch no more than 50-100 products per request to avoid timeouts. The pipeline will automatically create a background job to process the fetched items.
            </p>
          </div>

          {lastResult && (
            <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-lg shadow-black/5 animate-in fade-in slide-in-from-bottom-4">
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
                  <span className="text-muted-foreground text-sm">Skipped (Exists)</span>
                  <span className="font-medium text-amber-400">{lastResult.skipped}</span>
                </div>
                <div className="flex justify-between items-center py-2">
                  <span className="text-muted-foreground text-sm">Created Job</span>
                  <span className="font-medium text-primary">#{lastResult.jobId}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
