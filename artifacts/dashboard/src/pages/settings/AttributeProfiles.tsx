import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Filter, Plus, Trash2, Pencil, Loader2, X, Check, GripVertical } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { Modal } from "@/components/Modal";

interface ProfileAttr {
  id?: number;
  woo_attr_name: string;
  required: boolean;
  sort_order: number;
}

interface Profile {
  id: number;
  name: string;
  description: string | null;
  attributes: ProfileAttr[];
  created_at: string;
  updated_at: string;
}

type ProfileIn = { name: string; description: string | null; attributes: ProfileAttr[] };

const BLANK: ProfileIn = { name: "", description: null, attributes: [] };

export default function AttributeProfiles() {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [modal, setModal] = useState<{ open: boolean; profile: Profile | null }>({ open: false, profile: null });

  const { data, isLoading } = useQuery<{ profiles: Profile[] }>({
    queryKey: ["attr-profiles"],
    queryFn: () => fetch("/api/attr-profiles").then((r) => r.json()),
  });

  const save = useMutation({
    mutationFn: (payload: { id?: number; data: ProfileIn }) => {
      const { id, data: body } = payload;
      return fetch(id ? `/api/attr-profiles/${id}` : "/api/attr-profiles", {
        method: id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
        return r.json();
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["attr-profiles"] });
      toast({ title: "Profile saved" });
      setModal({ open: false, profile: null });
    },
    onError: (e: any) => toast({ title: "Error", description: e.message, variant: "destructive" }),
  });

  const del = useMutation({
    mutationFn: (id: number) =>
      fetch(`/api/attr-profiles/${id}`, { method: "DELETE" }).then((r) => { if (!r.ok) throw new Error(r.statusText); }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["attr-profiles"] }); toast({ title: "Profile deleted" }); },
    onError: (e: any) => toast({ title: "Error", description: e.message, variant: "destructive" }),
  });

  const profiles = data?.profiles ?? [];

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-start gap-4">
        <div>
          <h1 className="text-2xl font-display font-bold text-foreground">Attribute Profiles</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Named sets of WooCommerce attributes expected for a product category. Assign a profile to a category to guide AI extraction.
          </p>
        </div>
        <button
          onClick={() => setModal({ open: true, profile: null })}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:-translate-y-0.5 transition-all shadow-lg"
        >
          <Plus className="w-4 h-4" /> New Profile
        </button>
      </div>

      {isLoading ? (
        <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : profiles.length === 0 ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground">
          <Filter className="w-12 h-12 mx-auto opacity-20 mb-3" />
          <p className="text-sm font-medium text-foreground mb-1">No attribute profiles yet</p>
          <p className="text-sm">Create a profile for each product type (e.g. "Electronics", "Apparel") to tell the pipeline which attributes to extract.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          {profiles.map((p) => (
            <div key={p.id} className="bg-card border border-border/50 rounded-2xl p-5 shadow-lg shadow-black/5 flex flex-col gap-3">
              <div className="flex justify-between items-start">
                <div>
                  <h3 className="font-semibold text-foreground">{p.name}</h3>
                  {p.description && <p className="text-xs text-muted-foreground mt-0.5">{p.description}</p>}
                </div>
                <div className="flex gap-1 shrink-0">
                  <button onClick={() => setModal({ open: true, profile: p })} className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-secondary rounded-lg transition-colors">
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                  <button onClick={() => { if (confirm("Delete this profile?")) del.mutate(p.id); }} className="p-1.5 text-muted-foreground hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              {p.attributes.length > 0 ? (
                <div className="flex flex-wrap gap-1.5 pt-1 border-t border-border/30">
                  {p.attributes.sort((a, b) => a.sort_order - b.sort_order).map((a, i) => (
                    <span key={i} className={`text-xs px-2 py-0.5 rounded-full border ${a.required ? "bg-primary/10 text-primary border-primary/30" : "bg-secondary text-muted-foreground border-border"}`}>
                      {a.woo_attr_name}
                      {!a.required && <span className="ml-1 opacity-60">opt</span>}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">No attributes defined.</p>
              )}
            </div>
          ))}
        </div>
      )}

      <ProfileModal
        isOpen={modal.open}
        profile={modal.profile}
        onClose={() => setModal({ open: false, profile: null })}
        onSave={(d) => save.mutate({ id: modal.profile?.id, data: d })}
        saving={save.isPending}
      />
    </div>
  );
}

function ProfileModal({
  isOpen, profile, onClose, onSave, saving,
}: {
  isOpen: boolean;
  profile: Profile | null;
  onClose: () => void;
  onSave: (d: ProfileIn) => void;
  saving: boolean;
}) {
  const initAttrs = (): ProfileAttr[] =>
    profile ? profile.attributes.map((a) => ({ woo_attr_name: a.woo_attr_name, required: a.required, sort_order: a.sort_order })) : [];

  const [name, setName] = useState(profile?.name ?? "");
  const [desc, setDesc] = useState(profile?.description ?? "");
  const [attrs, setAttrs] = useState<ProfileAttr[]>(initAttrs);
  const [newAttr, setNewAttr] = useState("");

  const addAttr = () => {
    const v = newAttr.trim();
    if (!v || attrs.some((a) => a.woo_attr_name === v)) return;
    setAttrs((prev) => [...prev, { woo_attr_name: v, required: true, sort_order: prev.length }]);
    setNewAttr("");
  };

  const inp = "w-full bg-background border border-border rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-primary transition-all";

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={profile ? "Edit Profile" : "New Attribute Profile"}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSave({ name, description: desc || null, attributes: attrs.map((a, i) => ({ ...a, sort_order: i })) });
        }}
        className="space-y-4"
      >
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Profile Name</label>
          <input required className={inp} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Electronics, Apparel…" />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Description <span className="text-muted-foreground font-normal">(optional)</span></label>
          <input className={inp} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="When to use this profile…" />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">Attributes</label>
          <div className="flex gap-2">
            <input
              className={`${inp} flex-1`}
              value={newAttr}
              onChange={(e) => setNewAttr(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addAttr(); } }}
              placeholder="Attribute name (e.g. Colour)"
            />
            <button type="button" onClick={addAttr} className="px-3 py-2 rounded-xl bg-secondary hover:bg-secondary/80 text-sm font-medium transition-colors">
              <Plus className="w-4 h-4" />
            </button>
          </div>
          {attrs.length > 0 && (
            <div className="space-y-1 mt-2 max-h-52 overflow-y-auto pr-1">
              {attrs.map((a, i) => (
                <div key={i} className="flex items-center gap-2 bg-secondary/30 rounded-lg px-3 py-2">
                  <GripVertical className="w-3.5 h-3.5 text-muted-foreground/50 shrink-0" />
                  <span className="text-sm flex-1">{a.woo_attr_name}</span>
                  <button
                    type="button"
                    onClick={() => setAttrs((prev) => prev.map((x, j) => j === i ? { ...x, required: !x.required } : x))}
                    className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${a.required ? "bg-primary/10 text-primary border-primary/30" : "bg-background text-muted-foreground border-border"}`}
                  >
                    {a.required ? "required" : "optional"}
                  </button>
                  <button type="button" onClick={() => setAttrs((prev) => prev.filter((_, j) => j !== i))} className="text-muted-foreground hover:text-rose-400 transition-colors">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="pt-4 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="px-5 py-2.5 rounded-xl border border-border hover:bg-secondary font-medium text-sm">Cancel</button>
          <button type="submit" disabled={saving} className="px-6 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium text-sm shadow-lg hover:-translate-y-0.5 transition-all disabled:opacity-50 flex items-center gap-2">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            {saving ? "Saving…" : "Save Profile"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
