import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ChevronDown, Tag, Loader2, Play, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { Modal } from "@/components/Modal";
import { useToast } from "@/hooks/use-toast";

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

interface FetchTarget { id: string; name: string }

export default function SunskyCategories() {
  const [fetchTarget, setFetchTarget] = useState<FetchTarget | null>(null);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-display font-bold text-foreground">Sunsky Categories</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Browse the Sunsky product category tree. Click <strong>Fetch</strong> on any category to start a product import job from it.
        </p>
      </div>

      <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
        <div className="grid grid-cols-[1fr_120px_90px] gap-4 px-5 py-3 border-b border-border/50 text-xs font-semibold text-muted-foreground uppercase tracking-wider bg-secondary/20">
          <span>Category</span>
          <span className="text-right">Category ID</span>
          <span />
        </div>
        <CategoryLevel parentId="0" depth={0} onFetch={setFetchTarget} />
      </div>

      <FetchJobModal
        target={fetchTarget}
        onClose={() => setFetchTarget(null)}
      />
    </div>
  );
}

function CategoryLevel({
  parentId,
  depth,
  onFetch,
}: {
  parentId: string;
  depth: number;
  onFetch: (t: FetchTarget) => void;
}) {
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
        <p>No categories returned — using mock data or API unavailable.</p>
      </div>
    );

  return (
    <div className={cn(depth > 0 && "border-l-2 border-l-primary/20 ml-8")}>
      {data?.map((cat) => (
        <CategoryRow key={cat.id} cat={cat} depth={depth} onFetch={onFetch} />
      ))}
    </div>
  );
}

function CategoryRow({
  cat,
  depth,
  onFetch,
}: {
  cat: SunskyCategory;
  depth: number;
  onFetch: (t: FetchTarget) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <div
        className="grid grid-cols-[1fr_120px_90px] gap-4 items-center px-5 py-3 border-b border-border/20 last:border-b-0 hover:bg-secondary/20 transition-colors"
        style={{ paddingLeft: `${20 + depth * 20}px` }}
      >
        <div
          className="flex items-center gap-2 min-w-0 cursor-pointer"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? (
            <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          )}
          <Tag className="w-3.5 h-3.5 text-primary/60 shrink-0" />
          <span className="text-sm truncate">{cat.name}</span>
        </div>
        <span
          className="text-xs font-mono text-muted-foreground text-right cursor-pointer"
          onClick={() => setOpen((v) => !v)}
        >
          {cat.id}
        </span>
        <div className="flex justify-end">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onFetch({ id: cat.id, name: cat.name });
            }}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium transition-colors"
          >
            <Play className="w-3 h-3" /> Fetch
          </button>
        </div>
      </div>
      {open && <CategoryLevel parentId={cat.id} depth={depth + 1} onFetch={onFetch} />}
    </div>
  );
}

function FetchJobModal({
  target,
  onClose,
}: {
  target: FetchTarget | null;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [limit, setLimit] = useState("50");
  const [page, setPage] = useState("1");
  const [keyword, setKeyword] = useState("");

  const create = useMutation({
    mutationFn: () =>
      fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "fetch",
          config: {
            category_id: target?.id,
            limit: parseInt(limit) || 50,
            page: parseInt(page) || 1,
            ...(keyword.trim() ? { keyword: keyword.trim() } : {}),
          },
        }),
      }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
        return r.json();
      }),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["/api/jobs"] });
      toast({
        title: "Fetch job started",
        description: `Job #${job.id} — fetching from "${target?.name}" (cat: ${target?.id}).`,
      });
      onClose();
      setLimit("50");
      setPage("1");
      setKeyword("");
    },
    onError: (e: any) =>
      toast({ title: "Failed to start job", description: e.message, variant: "destructive" }),
  });

  const inp = "w-full bg-background border border-border rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-primary transition-all";

  return (
    <Modal isOpen={!!target} onClose={onClose} title="Fetch Products from Category">
      {target && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
          className="space-y-4"
        >
          {/* Category badge */}
          <div className="flex items-center gap-3 px-4 py-3 bg-primary/5 border border-primary/20 rounded-xl">
            <Tag className="w-4 h-4 text-primary shrink-0" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground truncate">{target.name}</p>
              <p className="text-xs font-mono text-muted-foreground">Category ID: {target.id}</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Products to fetch</label>
              <input
                type="number"
                min={1}
                max={500}
                className={inp}
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Start page</label>
              <input
                type="number"
                min={1}
                className={inp}
                value={page}
                onChange={(e) => setPage(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium">
              Keyword filter <span className="text-muted-foreground font-normal">(optional)</span>
            </label>
            <input
              type="text"
              className={inp}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="e.g. wireless, bluetooth…"
            />
          </div>

          <div className="pt-4 flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-5 py-2.5 rounded-xl border border-border hover:bg-secondary font-medium text-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={create.isPending}
              className="px-6 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium text-sm shadow-lg hover:-translate-y-0.5 transition-all disabled:opacity-50 flex items-center gap-2"
            >
              {create.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Check className="w-4 h-4" />
              )}
              {create.isPending ? "Starting…" : "Start Fetch Job"}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
