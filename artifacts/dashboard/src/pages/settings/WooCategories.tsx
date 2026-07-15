import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useStores } from "@/hooks/use-stores";
import { Tag, Store, Loader2 } from "lucide-react";

interface WooCategory {
  id: number;
  woo_id: number;
  name: string;
  slug: string;
  parent_id: number | null;
  product_count?: number;
}

export default function WooCategories() {
  const { data: stores } = useStores();
  const [storeId, setStoreId] = useState<string>("");

  const { data: categories, isLoading } = useQuery<WooCategory[]>({
    queryKey: ["woo-categories", storeId],
    queryFn: () =>
      fetch(`/api/stores/${storeId}/categories`).then((r) => {
        if (!r.ok) throw new Error(r.statusText);
        return r.json();
      }),
    enabled: !!storeId,
    staleTime: 2 * 60 * 1000,
  });

  const topLevel = categories?.filter((c) => !c.parent_id) ?? [];
  const children = (parentId: number) => categories?.filter((c) => c.parent_id === parentId) ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-display font-bold text-foreground">WooCommerce Categories</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Categories pulled from your WooCommerce stores. Use <strong>Pull from WooCommerce</strong> on the Stores page to refresh.
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
          <p className="text-sm">Select a store to view its categories.</p>
        </div>
      ) : isLoading ? (
        <div className="py-16 flex justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-primary" />
        </div>
      ) : !categories?.length ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground">
          <Tag className="w-12 h-12 mx-auto opacity-20 mb-3" />
          <p className="text-sm">No categories synced yet — use Pull from WooCommerce on the Stores page.</p>
        </div>
      ) : (
        <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
          <div className="grid grid-cols-[1fr_80px_80px] gap-4 px-5 py-3 border-b border-border/50 text-xs font-semibold text-muted-foreground uppercase tracking-wider bg-secondary/20">
            <span>Category</span>
            <span className="text-right">WooID</span>
            <span className="text-right">Products</span>
          </div>
          {topLevel.map((cat) => (
            <CategoryRow key={cat.id} cat={cat} depth={0} subCats={children(cat.woo_id)} allCats={categories} />
          ))}
        </div>
      )}
    </div>
  );
}

function CategoryRow({
  cat,
  depth,
  subCats,
  allCats,
}: {
  cat: WooCategory;
  depth: number;
  subCats: WooCategory[];
  allCats: WooCategory[];
}) {
  return (
    <>
      <div
        className="grid grid-cols-[1fr_80px_80px] gap-4 items-center px-5 py-3 border-b border-border/20 last:border-b-0 hover:bg-secondary/20"
        style={{ paddingLeft: `${20 + depth * 20}px` }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Tag className="w-3.5 h-3.5 text-primary/60 shrink-0" />
          <span className="text-sm truncate">{cat.name}</span>
          {subCats.length > 0 && (
            <span className="text-xs text-muted-foreground ml-1">({subCats.length})</span>
          )}
        </div>
        <span className="text-xs font-mono text-muted-foreground text-right">{cat.woo_id}</span>
        <span className="text-xs text-muted-foreground text-right">{cat.product_count ?? "—"}</span>
      </div>
      {subCats.map((child) => (
        <CategoryRow
          key={child.id}
          cat={child}
          depth={depth + 1}
          subCats={allCats.filter((c) => c.parent_id === child.woo_id)}
          allCats={allCats}
        />
      ))}
    </>
  );
}
