import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Filter, Plus, Trash2, Pencil, Loader2, X, Check } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { Modal } from "@/components/Modal";

interface Rule {
  id: number;
  woo_attr_name: string;
  source_fields: string;
  instruction: string;
  confidence_threshold: number;
  if_not_found: string;
  default_value: string | null;
  sort_order: number;
}

type RuleIn = Omit<Rule, "id">;

const BLANK: RuleIn = {
  woo_attr_name: "",
  source_fields: "both",
  instruction: "",
  confidence_threshold: 0.7,
  if_not_found: "flag",
  default_value: null,
  sort_order: 0,
};

const srcLabels: Record<string, string> = { title: "Title", specs: "Specs", both: "Both" };
const notFoundLabels: Record<string, string> = { leave_blank: "Leave blank", flag: "Flag for review", use_default: "Use default" };

export default function ExtractionRules() {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [modal, setModal] = useState<{ open: boolean; rule: Rule | null }>({ open: false, rule: null });

  const { data, isLoading } = useQuery<{ rules: Rule[] }>({
    queryKey: ["attr-rules"],
    queryFn: () => fetch("/api/attr-rules").then((r) => r.json()),
  });

  const save = useMutation({
    mutationFn: (payload: { id?: number; data: RuleIn }) => {
      const { id, data: body } = payload;
      return fetch(id ? `/api/attr-rules/${id}` : "/api/attr-rules", {
        method: id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
        return r.json();
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["attr-rules"] });
      toast({ title: "Rule saved" });
      setModal({ open: false, rule: null });
    },
    onError: (e: any) => toast({ title: "Error", description: e.message, variant: "destructive" }),
  });

  const del = useMutation({
    mutationFn: (id: number) =>
      fetch(`/api/attr-rules/${id}`, { method: "DELETE" }).then((r) => { if (!r.ok) throw new Error(r.statusText); }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["attr-rules"] }); toast({ title: "Rule deleted" }); },
    onError: (e: any) => toast({ title: "Error", description: e.message, variant: "destructive" }),
  });

  const rules = data?.rules ?? [];

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-start gap-4">
        <div>
          <h1 className="text-2xl font-display font-bold text-foreground">Extraction Rules</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Tell the AI how to extract each WooCommerce attribute from Sunsky product data. One rule per attribute.
          </p>
        </div>
        <button
          onClick={() => setModal({ open: true, rule: null })}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:-translate-y-0.5 transition-all shadow-lg"
        >
          <Plus className="w-4 h-4" /> Add Rule
        </button>
      </div>

      {isLoading ? (
        <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : rules.length === 0 ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground">
          <Filter className="w-12 h-12 mx-auto opacity-20 mb-3" />
          <p className="text-sm font-medium text-foreground mb-1">No extraction rules yet</p>
          <p className="text-sm">Add a rule for each WooCommerce attribute you want the AI to extract (e.g. Colour, Material, Weight).</p>
        </div>
      ) : (
        <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
          <div className="grid grid-cols-[1fr_80px_80px_120px_80px] gap-3 px-5 py-3 border-b border-border/50 text-xs font-semibold text-muted-foreground uppercase tracking-wider bg-secondary/20">
            <span>Attribute / Instruction</span>
            <span>Source</span>
            <span className="text-right">Confidence</span>
            <span>If Not Found</span>
            <span />
          </div>
          {rules.map((rule) => (
            <div key={rule.id} className="grid grid-cols-[1fr_80px_80px_120px_80px] gap-3 items-start px-5 py-4 border-b border-border/20 last:border-b-0 hover:bg-secondary/10">
              <div>
                <p className="text-sm font-semibold text-foreground">{rule.woo_attr_name}</p>
                {rule.instruction && <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{rule.instruction}</p>}
                {rule.default_value && (
                  <p className="text-xs text-amber-400 mt-1">Default: <span className="font-mono">{rule.default_value}</span></p>
                )}
              </div>
              <span className="text-xs bg-secondary px-2 py-1 rounded-lg text-muted-foreground w-fit mt-0.5">{srcLabels[rule.source_fields] ?? rule.source_fields}</span>
              <span className="text-xs text-muted-foreground text-right mt-1">{Math.round(rule.confidence_threshold * 100)}%</span>
              <span className="text-xs text-muted-foreground mt-1">{notFoundLabels[rule.if_not_found] ?? rule.if_not_found}</span>
              <div className="flex gap-1 mt-0.5">
                <button onClick={() => setModal({ open: true, rule })} className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-secondary rounded-lg transition-colors">
                  <Pencil className="w-3.5 h-3.5" />
                </button>
                <button onClick={() => { if (confirm("Delete this rule?")) del.mutate(rule.id); }} className="p-1.5 text-muted-foreground hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <RuleModal
        isOpen={modal.open}
        rule={modal.rule}
        onClose={() => setModal({ open: false, rule: null })}
        onSave={(data) => save.mutate({ id: modal.rule?.id, data })}
        saving={save.isPending}
      />
    </div>
  );
}

function RuleModal({
  isOpen, rule, onClose, onSave, saving,
}: {
  isOpen: boolean;
  rule: Rule | null;
  onClose: () => void;
  onSave: (d: RuleIn) => void;
  saving: boolean;
}) {
  const [form, setForm] = useState<RuleIn>(BLANK);

  useState(() => {
    if (rule) {
      setForm({
        woo_attr_name: rule.woo_attr_name,
        source_fields: rule.source_fields,
        instruction: rule.instruction,
        confidence_threshold: rule.confidence_threshold,
        if_not_found: rule.if_not_found,
        default_value: rule.default_value,
        sort_order: rule.sort_order,
      });
    } else {
      setForm(BLANK);
    }
  });

  const inp = "w-full bg-background border border-border rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-primary transition-all";
  const sel = `${inp} cursor-pointer`;

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={rule ? "Edit Rule" : "Add Extraction Rule"}>
      <form
        onSubmit={(e) => { e.preventDefault(); onSave(form); }}
        className="space-y-4"
      >
        <div className="space-y-1.5">
          <label className="text-sm font-medium">WooCommerce Attribute Name</label>
          <input required className={inp} value={form.woo_attr_name} onChange={(e) => setForm({ ...form, woo_attr_name: e.target.value })} placeholder="e.g. Colour, Material, Weight" />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Extraction Instruction</label>
          <textarea rows={3} className={inp} value={form.instruction} onChange={(e) => setForm({ ...form, instruction: e.target.value })} placeholder="Describe to the AI how to find this value…" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Source Fields</label>
            <select className={sel} value={form.source_fields} onChange={(e) => setForm({ ...form, source_fields: e.target.value })}>
              <option value="both">Both (title + specs)</option>
              <option value="title">Title only</option>
              <option value="specs">Specs only</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Confidence Threshold</label>
            <div className="flex items-center gap-2">
              <input type="range" min={0.1} max={1} step={0.05} value={form.confidence_threshold}
                onChange={(e) => setForm({ ...form, confidence_threshold: parseFloat(e.target.value) })}
                className="flex-1 accent-primary" />
              <span className="text-sm font-mono w-10 text-right">{Math.round(form.confidence_threshold * 100)}%</span>
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">If Not Found</label>
            <select className={sel} value={form.if_not_found} onChange={(e) => setForm({ ...form, if_not_found: e.target.value })}>
              <option value="flag">Flag for review</option>
              <option value="leave_blank">Leave blank</option>
              <option value="use_default">Use default value</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Default Value</label>
            <input className={inp} value={form.default_value ?? ""} onChange={(e) => setForm({ ...form, default_value: e.target.value || null })} placeholder="(optional)" disabled={form.if_not_found !== "use_default"} />
          </div>
        </div>
        <div className="pt-4 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="px-5 py-2.5 rounded-xl border border-border hover:bg-secondary font-medium text-sm">Cancel</button>
          <button type="submit" disabled={saving} className="px-6 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium text-sm shadow-lg hover:-translate-y-0.5 transition-all disabled:opacity-50 flex items-center gap-2">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            {saving ? "Saving…" : "Save Rule"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
