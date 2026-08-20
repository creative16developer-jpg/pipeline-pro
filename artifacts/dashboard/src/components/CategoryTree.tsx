import { useState, useEffect, useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export interface WooOpt { id: number; name: string; name_en?: string | null; parent_id: number }
export interface WooCatEntry { id: number; name: string }
export interface TreeNode { opt: WooOpt; children: TreeNode[]; depth: number }

export function buildTree(opts: WooOpt[]): TreeNode[] {
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

/**
 * Multi-select category tree with a designated primary category.
 * Client feedback item #9: "In the last step – Review can't override the
 * whole category level include main and subcategories. Can override only
 * last subcategory. The last one need to be primary." -- lets the
 * operator check a full path (main category + subcategory + grandchild,
 * as many levels as needed), with the deepest/most-specific one they
 * pick automatically offered as "Set primary".
 */
export function MiniCatTree({ tree, selected, primaryId, onToggle, onSetPrimary }: {
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
            title={node.opt.name_en ? `${node.opt.name} — ${node.opt.name_en}` : node.opt.name}
            className={cn("text-xs cursor-pointer flex-1 min-w-0 truncate",
              checked ? (isPrimary ? "text-emerald-400 font-medium" : "text-blue-400") : "text-foreground"
            )}
          >
            {node.opt.name}
            {node.opt.name_en && (
              <span className="text-muted-foreground/60 font-normal"> ({node.opt.name_en})</span>
            )}
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
