import { useState } from "react";
import { cn } from "@/lib/utils";

// Client feedback: "After upload CSV need to have preview of the table with
// the results". Shown after a CSV upload on both the CSV Import page and
// New Pipeline: every row of the file with the values as imported, whether
// the product was new / updated / skipped, and per-row warnings (invalid
// values ignored, Sale Price not lower than Price, duplicate Sunsky SKU).

export type CsvResultRow = {
  row: number;
  sunsky_sku: string;
  site_sku: string;
  csv_title: string;
  price: string | null;
  sale_price: string | null;
  qty: number | null;
  result?: "new" | "updated";
  warnings?: string[];
};
export type CsvSkippedRow = { row: number; site_sku: string; csv_title: string; reason: string };
export type CsvUploadResponse = {
  imported: number;
  errors: string[];
  preview?: any[];
  results?: CsvResultRow[];
  skipped?: CsvSkippedRow[];
  summary?: { new: number; updated: number; skipped: number; with_warnings: number };
  encoding_warning?: string;
};

type TableRow = CsvResultRow & { status: "new" | "updated" | "skipped"; reason?: string };

export function buildCsvResultRows(res: CsvUploadResponse): TableRow[] {
  const rows: TableRow[] = (res.results ?? []).map(r => ({ ...r, status: r.result ?? "updated" }));
  for (const s of res.skipped ?? []) {
    rows.push({
      row: s.row, sunsky_sku: "", site_sku: s.site_sku, csv_title: s.csv_title,
      price: null, sale_price: null, qty: null, status: "skipped", reason: s.reason,
    });
  }
  return rows.sort((a, b) => a.row - b.row);
}

export function CsvImportResults({ result }: { result: CsvUploadResponse }) {
  const [onlyProblems, setOnlyProblems] = useState(false);
  const all = buildCsvResultRows(result);
  const hasProblem = (r: TableRow) => r.status === "skipped" || (r.warnings?.length ?? 0) > 0;
  const rows = onlyProblems ? all.filter(hasProblem) : all;
  const s = result.summary ?? {
    new: all.filter(r => r.status === "new").length,
    updated: all.filter(r => r.status === "updated").length,
    skipped: all.filter(r => r.status === "skipped").length,
    with_warnings: all.filter(r => (r.warnings?.length ?? 0) > 0).length,
  };
  const pill = "px-2.5 py-1 rounded-full font-medium";
  const badge: Record<TableRow["status"], string> = {
    new: "bg-emerald-500/15 text-emerald-400",
    updated: "bg-sky-500/15 text-sky-400",
    skipped: "bg-red-500/15 text-red-400",
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <span className={cn(pill, "bg-emerald-500/20 text-emerald-400")}>{s.new} new</span>
        <span className={cn(pill, "bg-sky-500/20 text-sky-400")}>{s.updated} updated</span>
        {s.skipped > 0 && <span className={cn(pill, "bg-red-500/20 text-red-400")}>{s.skipped} skipped</span>}
        {s.with_warnings > 0 && <span className={cn(pill, "bg-amber-500/20 text-amber-400")}>{s.with_warnings} with warnings</span>}
        {(s.skipped > 0 || s.with_warnings > 0) && (
          <label className="ml-auto flex items-center gap-1.5 text-muted-foreground cursor-pointer">
            <input type="checkbox" checked={onlyProblems} onChange={e => setOnlyProblems(e.target.checked)} />
            Only rows with problems
          </label>
        )}
      </div>

      {result.encoding_warning && (
        <div className="text-xs rounded-lg border border-red-500/30 bg-red-500/5 text-red-300 px-3 py-2">{result.encoding_warning}</div>
      )}

      <div className="overflow-auto max-h-[28rem] rounded-xl border border-border">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card">
            <tr className="border-b border-border bg-secondary/30">
              {["Row", "Result", "Sunsky SKU", "Site SKU", "Title", "Price", "Sale Price", "QTY", "Notes"].map(h => (
                <th key={h} className="text-left px-3 py-2 text-muted-foreground font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={`${r.row}-${r.status}`} className={cn("border-b border-border/50 last:border-0", hasProblem(r) && "bg-amber-500/[0.04]")}>
                <td className="px-3 py-2 text-muted-foreground">{r.row}</td>
                <td className="px-3 py-2"><span className={cn("px-2 py-0.5 rounded-md text-[11px] font-medium", badge[r.status])}>{r.status}</span></td>
                <td className="px-3 py-2 font-mono text-primary whitespace-nowrap">{r.sunsky_sku || "—"}</td>
                <td className="px-3 py-2 font-mono text-muted-foreground whitespace-nowrap">{r.site_sku || "—"}</td>
                <td className="px-3 py-2 text-foreground min-w-[220px]">{r.csv_title || "—"}</td>
                <td className="px-3 py-2 whitespace-nowrap">{r.price ?? "—"}</td>
                <td className="px-3 py-2 whitespace-nowrap">{r.sale_price ?? "—"}</td>
                <td className="px-3 py-2 whitespace-nowrap">{r.qty ?? "—"}</td>
                <td className="px-3 py-2 min-w-[220px]">
                  {r.status === "skipped" && <span className="text-red-400">{r.reason}</span>}
                  {(r.warnings ?? []).map((w, i) => <div key={i} className="text-amber-400">{w}</div>)}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={9} className="px-3 py-6 text-center text-muted-foreground">No rows to show.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
