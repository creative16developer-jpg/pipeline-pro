import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useStores } from "@/hooks/use-stores";
import { ArrowLeftRight, Store, Loader2, Trash2, Info } from "lucide-react";
import { format } from "date-fns";
import { useToast } from "@/hooks/use-toast";

interface CategoryMapping {
  id: number;
  sunsky_cat: string;
  woo_cats: { id: number; name: string }[];
  primary_woo_cat_id: number | null;
  times_used: number;
  last_used_at: string | null;
  updated_at: string | null;
}

export default function CategoryMapping() {
  const { data: stores } = useStores();
  const [storeId, setStoreId] = useState<string>("");
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery<{ mappings: CategoryMapping[] }>({
    queryKey: ["category-mappings", storeId],
    queryFn: () =>
      fetch(`/api/stores/${storeId}/category-mappings`).then((r) => {
        if (!r.ok) throw new Error(r.statusText);
        return r.json();
      }),
    enabled: !!storeId,
  });

  const deleteMapping = useMutation({
    mutationFn: (id: number) =>
      fetch(`/api/stores/${storeId}/category-mappings/${id}`, { method: "DELETE" }).then((r) => {
        if (!r.ok) throw new Error(r.statusText);
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["category-mappings", storeId] });
      toast({ title: "Mapping deleted" });
    },
    onError: (e: any) => toast({ title: "Error", description: e.message, variant: "destructive" }),
  });

  const mappings = data?.mappings ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-display font-bold text-foreground">Category Mapping</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Saved Sunsky → WooCommerce category mappings per store. These are built automatically during pipeline review pauses.
          </p>
        </div>
        <select
          value={storeId}
          onChange={(e) => setStoreId(e.target.value)}
          className="bg-background border border-border rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-primary min-w-[180px]"
        >
          <option value="">— Select store —</option>
          {stores?.map((s: any) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </div>

      {!storeId ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground">
          <Store className="w-12 h-12 mx-auto opacity-20 mb-3" />
          <p className="text-sm">Select a store to view its category mappings.</p>
        </div>
      ) : isLoading ? (
        <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : mappings.length === 0 ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground">
          <ArrowLeftRight className="w-12 h-12 mx-auto opacity-20 mb-3" />
          <p className="text-sm font-medium text-foreground mb-1">No mappings yet</p>
          <p className="text-sm">Run a pipeline — unmapped categories will trigger a review pause where you can set the mapping. It will be saved here for future runs.</p>
        </div>
      ) : (
        <>
          <div className="flex items-start gap-2 px-4 py-3 bg-primary/5 border border-primary/20 rounded-xl text-sm text-primary">
            <Info className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{mappings.length} mapping{mappings.length !== 1 && "s"} saved — these are used automatically on future pipeline runs.</span>
          </div>
          <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
            <div className="grid grid-cols-[1fr_1fr_80px_120px_40px] gap-3 px-5 py-3 border-b border-border/50 text-xs font-semibold text-muted-foreground uppercase tracking-wider bg-secondary/20">
              <span>Sunsky Category</span>
              <span>WooCommerce Categories</span>
              <span className="text-right">Used</span>
              <span className="text-right">Last Used</span>
              <span />
            </div>
            {mappings.map((m) => (
              <div key={m.id} className="grid grid-cols-[1fr_1fr_80px_120px_40px] gap-3 items-center px-5 py-3.5 border-b border-border/20 last:border-b-0 hover:bg-secondary/10">
                <span className="text-sm font-mono text-foreground">{m.sunsky_cat}</span>
                <div className="flex flex-wrap gap-1">
                  {m.woo_cats.map((c) => (
                    <span key={c.id} className={`text-xs px-2 py-0.5 rounded-full border ${c.id === m.primary_woo_cat_id ? "bg-primary/10 text-primary border-primary/30" : "bg-secondary text-muted-foreground border-border"}`}>
                      {c.name}
                    </span>
                  ))}
                </div>
                <span className="text-xs text-muted-foreground text-right">{m.times_used}×</span>
                <span className="text-xs text-muted-foreground text-right">
                  {m.last_used_at ? format(new Date(m.last_used_at), "MMM d, yy") : "—"}
                </span>
                <button
                  onClick={() => { if (confirm("Delete this mapping?")) deleteMapping.mutate(m.id); }}
                  className="p-1.5 text-muted-foreground hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
