import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useStores } from "@/hooks/use-stores";
import { BarChart3, Store, Loader2, Save } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

interface InventoryConfig {
  id: number | null;
  store_id: number;
  weight_unit: string;
  dimension_unit: string;
  weight_null: string;
  length_null: string;
  width_null: string;
  height_null: string;
  weight_default: string | null;
  length_default: string | null;
  width_default: string | null;
  height_default: string | null;
  updated_at: string | null;
}

const NULL_OPTS = [
  { value: "leave_blank", label: "Leave blank" },
  { value: "use_default", label: "Use default value" },
  { value: "skip", label: "Skip product" },
];

export default function InventoryMapping() {
  const { data: stores } = useStores();
  const [storeId, setStoreId] = useState<string>("");
  const { toast } = useToast();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery<InventoryConfig>({
    queryKey: ["inventory-mapping", storeId],
    queryFn: () => fetch(`/api/stores/${storeId}/inventory-mapping`).then((r) => r.json()),
    enabled: !!storeId,
  });

  const [form, setForm] = useState<Partial<InventoryConfig>>({});
  useEffect(() => { if (data) setForm(data); }, [data]);

  const save = useMutation({
    mutationFn: () =>
      fetch(`/api/stores/${storeId}/inventory-mapping`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          weight_unit: form.weight_unit ?? "kg",
          dimension_unit: form.dimension_unit ?? "cm",
          weight_null: form.weight_null ?? "leave_blank",
          length_null: form.length_null ?? "leave_blank",
          width_null: form.width_null ?? "leave_blank",
          height_null: form.height_null ?? "leave_blank",
          weight_default: form.weight_default || null,
          length_default: form.length_default || null,
          width_default: form.width_default || null,
          height_default: form.height_default || null,
        }),
      }).then(async (r) => {
        if (!r.ok) throw new Error(r.statusText);
        return r.json();
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inventory-mapping", storeId] });
      toast({ title: "Inventory mapping saved" });
    },
    onError: (e: any) => toast({ title: "Error", description: e.message, variant: "destructive" }),
  });

  const sel = "bg-background border border-border rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-primary transition-all";
  const inp = "bg-background border border-border rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-primary transition-all w-32";

  const dimRow = (label: string, nullKey: keyof InventoryConfig, defKey: keyof InventoryConfig) => (
    <div className="grid grid-cols-[100px_1fr_180px] gap-4 items-center py-3.5 border-b border-border/20 last:border-b-0">
      <span className="text-sm font-medium">{label}</span>
      <select
        className={sel}
        value={(form[nullKey] as string) ?? "leave_blank"}
        onChange={(e) => setForm({ ...form, [nullKey]: e.target.value })}
      >
        {NULL_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <input
        className={inp}
        placeholder="Default value"
        value={(form[defKey] as string) ?? ""}
        onChange={(e) => setForm({ ...form, [defKey]: e.target.value || null })}
        disabled={(form[nullKey] as string) !== "use_default"}
      />
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-display font-bold text-foreground">Inventory Mapping</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Configure how Sunsky weight and dimensions are mapped to WooCommerce shipping fields per store.
          </p>
        </div>
        <select
          value={storeId}
          onChange={(e) => setStoreId(e.target.value)}
          className="bg-background border border-border rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-primary min-w-[180px]"
        >
          <option value="">— Select store —</option>
          {stores?.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>

      {!storeId ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground">
          <Store className="w-12 h-12 mx-auto opacity-20 mb-3" />
          <p className="text-sm">Select a store to configure its inventory mapping.</p>
        </div>
      ) : isLoading ? (
        <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : (
        <div className="space-y-5">
          {/* Units */}
          <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-lg shadow-black/5">
            <h2 className="text-sm font-semibold text-foreground mb-4">Units</h2>
            <div className="grid grid-cols-2 gap-5">
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground font-medium">Weight Unit</label>
                <select className={`${sel} w-full`} value={form.weight_unit ?? "kg"} onChange={(e) => setForm({ ...form, weight_unit: e.target.value })}>
                  <option value="kg">kg</option>
                  <option value="g">g</option>
                  <option value="lbs">lbs</option>
                  <option value="oz">oz</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground font-medium">Dimension Unit</label>
                <select className={`${sel} w-full`} value={form.dimension_unit ?? "cm"} onChange={(e) => setForm({ ...form, dimension_unit: e.target.value })}>
                  <option value="cm">cm</option>
                  <option value="m">m</option>
                  <option value="mm">mm</option>
                  <option value="in">in</option>
                  <option value="yd">yd</option>
                </select>
              </div>
            </div>
          </div>

          {/* Null handling */}
          <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
            <div className="px-5 py-4 border-b border-border/50 bg-secondary/20">
              <h2 className="text-sm font-semibold text-foreground">When Value is Missing</h2>
              <p className="text-xs text-muted-foreground mt-0.5">What to do when Sunsky doesn't provide a measurement.</p>
            </div>
            <div className="px-5">
              <div className="grid grid-cols-[100px_1fr_180px] gap-4 py-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                <span>Field</span>
                <span>Action</span>
                <span>Default value</span>
              </div>
              {dimRow("Weight", "weight_null", "weight_default")}
              {dimRow("Length", "length_null", "length_default")}
              {dimRow("Width", "width_null", "width_default")}
              {dimRow("Height", "height_null", "height_default")}
            </div>
          </div>

          <div className="flex justify-end">
            <button
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:-translate-y-0.5 transition-all shadow-lg disabled:opacity-50"
            >
              {save.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
