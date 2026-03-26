import { cn } from "@/lib/utils";

export function StatusBadge({ status, className }: { status: string, className?: string }) {
  const normalized = status.toLowerCase();
  
  const colors: Record<string, string> = {
    pending: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    processing: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    running: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    processed: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
    uploaded: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    completed: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    active: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    sync: "bg-fuchsia-500/10 text-fuchsia-400 border-fuchsia-500/20",
    fetch: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
    upload: "bg-orange-500/10 text-orange-400 border-orange-500/20",
    process: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    failed: "bg-rose-500/10 text-rose-400 border-rose-500/20",
    error: "bg-rose-500/10 text-rose-400 border-rose-500/20",
    cancelled: "bg-slate-500/10 text-slate-400 border-slate-500/20",
    inactive: "bg-slate-500/10 text-slate-400 border-slate-500/20",
  };

  const colorStyle = colors[normalized] || "bg-slate-500/10 text-slate-400 border-slate-500/20";

  return (
    <span className={cn(
      "px-2.5 py-1 rounded-full text-[11px] font-medium border uppercase tracking-wider flex items-center gap-1.5 w-fit",
      colorStyle,
      className
    )}>
      <span className="w-1.5 h-1.5 rounded-full bg-current shadow-[0_0_8px_currentColor]" />
      {status}
    </span>
  );
}
