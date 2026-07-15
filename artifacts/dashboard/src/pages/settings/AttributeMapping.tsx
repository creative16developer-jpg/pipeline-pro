import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useStores } from "@/hooks/use-stores";
import { Layers, Store, Loader2, ChevronDown, ChevronRight, Tag } from "lucide-react";

interface WooAttribute {
  id: number;
  woo_id: number;
  name: string;
  slug: string;
  terms: { id: number; woo_id: number; name: string; slug: string }[];
}

export default function AttributeMapping() {
  const { data: stores } = useStores();
  const [storeId, setStoreId] = useState<string>("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const { data: attrs, isLoading } = useQuery<WooAttribute[]>({
    queryKey: ["woo-attributes", storeId],
    queryFn: () =>
      fetch(`/api/stores/${storeId}/woo-attributes`).then((r) => {
        if (!r.ok) throw new Error(r.statusText);
        return r.json();
      }),
    enabled: !!storeId,
    staleTime: 2 * 60 * 1000,
  });

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-display font-bold text-foreground">WooCommerce Attributes</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Product attributes and their terms pulled from WooCommerce. These are used by Extraction Rules to map AI-extracted values.
            Use <strong>Pull from WooCommerce</strong> on the Stores page to refresh.
          </p>
        </div>
        <select
          value={storeId}
          onChange={(e) => { setStoreId(e.target.value); setExpanded(new Set()); }}
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
          <p className="text-sm">Select a store to view its attributes.</p>
        </div>
      ) : isLoading ? (
        <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : !attrs?.length ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground">
          <Layers className="w-12 h-12 mx-auto opacity-20 mb-3" />
          <p className="text-sm font-medium text-foreground mb-1">No attributes synced</p>
          <p className="text-sm">Use Pull from WooCommerce on the Stores page to import attributes &amp; terms.</p>
        </div>
      ) : (
        <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
          <div className="grid grid-cols-[1fr_120px_80px] gap-4 px-5 py-3 border-b border-border/50 text-xs font-semibold text-muted-foreground uppercase tracking-wider bg-secondary/20">
            <span>Attribute</span>
            <span>Slug</span>
            <span className="text-right">Terms</span>
          </div>
          {attrs.map((attr) => (
            <div key={attr.id}>
              <div
                className="grid grid-cols-[1fr_120px_80px] gap-4 items-center px-5 py-3.5 border-b border-border/20 hover:bg-secondary/20 cursor-pointer transition-colors"
                onClick={() => toggle(attr.id)}
              >
                <div className="flex items-center gap-2">
                  {expanded.has(attr.id) ? (
                    <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                  )}
                  <Layers className="w-3.5 h-3.5 text-primary/60" />
                  <span className="text-sm font-medium">{attr.name}</span>
                </div>
                <span className="text-xs font-mono text-muted-foreground">{attr.slug}</span>
                <span className="text-xs text-muted-foreground text-right">{attr.terms.length}</span>
              </div>
              {expanded.has(attr.id) && attr.terms.length > 0 && (
                <div className="border-b border-border/20 bg-secondary/10 px-5 py-3">
                  <div className="flex flex-wrap gap-1.5 pl-8">
                    {attr.terms.map((t) => (
                      <span key={t.id} className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-secondary border border-border text-muted-foreground">
                        <Tag className="w-2.5 h-2.5" />
                        {t.name}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
