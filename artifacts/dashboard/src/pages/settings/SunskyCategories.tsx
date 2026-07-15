import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ChevronDown, Tag, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface SunskyCategory {
  id: string;
  name: string;
  parent_id: string;
}

function fetchCats(parentId: string): Promise<SunskyCategory[]> {
  return fetch(`/api/sunsky/categories?parent_id=${parentId}`).then((r) => {
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  });
}

export default function SunskyCategories() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-display font-bold text-foreground">Sunsky Categories</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Browse the Sunsky product category tree. Click a row to expand its sub-categories. Use the category ID when configuring fetch jobs.
        </p>
      </div>
      <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
        <div className="grid grid-cols-[1fr_120px] gap-4 px-5 py-3 border-b border-border/50 text-xs font-semibold text-muted-foreground uppercase tracking-wider bg-secondary/20">
          <span>Category</span>
          <span className="text-right">Category ID</span>
        </div>
        <CategoryLevel parentId="0" depth={0} />
      </div>
    </div>
  );
}

function CategoryLevel({ parentId, depth }: { parentId: string; depth: number }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["sunsky-cats", parentId],
    queryFn: () => fetchCats(parentId),
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading)
    return (
      <div className="flex items-center gap-2 text-muted-foreground text-sm py-4 px-5">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading…
      </div>
    );

  if (isError)
    return <div className="text-rose-400 text-sm py-4 px-5">Failed to load. Check Sunsky credentials.</div>;

  if (!data?.length && depth === 0)
    return (
      <div className="py-12 text-center text-muted-foreground text-sm">
        <Tag className="w-10 h-10 mx-auto opacity-20 mb-3" />
        No categories returned — using mock data or API unavailable.
      </div>
    );

  return (
    <div className={cn(depth > 0 && "border-l-2 border-l-primary/20 ml-8")}>
      {data?.map((cat) => <CategoryRow key={cat.id} cat={cat} depth={depth} />)}
    </div>
  );
}

function CategoryRow({ cat, depth }: { cat: SunskyCategory; depth: number }) {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <div
        className="grid grid-cols-[1fr_120px] gap-4 items-center px-5 py-3 hover:bg-secondary/30 cursor-pointer border-b border-border/20 last:border-b-0 transition-colors"
        style={{ paddingLeft: `${20 + depth * 20}px` }}
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex items-center gap-2 min-w-0">
          {open ? (
            <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          )}
          <Tag className="w-3.5 h-3.5 text-primary/60 shrink-0" />
          <span className="text-sm truncate">{cat.name}</span>
        </div>
        <span className="text-xs font-mono text-muted-foreground text-right">{cat.id}</span>
      </div>
      {open && <CategoryLevel parentId={cat.id} depth={depth + 1} />}
    </div>
  );
}
