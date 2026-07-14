import { useState } from "react";
import {
  useStores,
  useCreateStore,
  useDeleteStore,
  useTestConnection,
  usePullFromWooCommerce,
} from "@/hooks/use-stores";
import { Modal } from "@/components/Modal";
import {
  Store as StoreIcon,
  Plus,
  Trash2,
  Link as LinkIcon,
  AlertCircle,
  Download,
  ChevronDown,
  ChevronUp,
  Tag,
  Layers,
} from "lucide-react";
import { format } from "date-fns";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

export default function Stores() {
  const { data: stores, isLoading } = useStores();
  const [isModalOpen, setIsModalOpen] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between gap-4 items-start sm:items-center">
        <div>
          <h1 className="text-2xl font-display font-bold text-foreground">Stores</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Manage connected WooCommerce stores.
          </p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="px-5 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2 text-sm"
        >
          <Plus className="w-4 h-4" /> Add Store
        </button>
      </div>

      {isLoading ? (
        <div className="py-20 flex justify-center">
          <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      ) : stores?.length === 0 ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground shadow-lg shadow-black/5">
          <StoreIcon className="w-16 h-16 mx-auto opacity-20 mb-4" />
          <h3 className="text-lg font-medium text-foreground mb-1">No stores configured</h3>
          <p className="text-sm">Add your first WooCommerce store to start importing products.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          {stores?.map((store) => (
            <StoreCard key={store.id} store={store} />
          ))}
        </div>
      )}

      <AddStoreModal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)} />
    </div>
  );
}

function StoreCard({ store }: { store: any }) {
  const testConn = useTestConnection();
  const pull = usePullFromWooCommerce();
  const deleteStore = useDeleteStore();
  const { toast } = useToast();

  const [pullOpen, setPullOpen] = useState(false);
  const [pullCategories, setPullCategories] = useState(true);
  const [pullAttributes, setPullAttributes] = useState(true);
  const [lastPull, setLastPull] = useState<{
    at: Date;
    categories?: number;
    attributes?: number;
  } | null>(null);

  const handleTest = async () => {
    try {
      const res = await testConn.mutateAsync({ id: store.id });
      toast({
        title: res.success ? "Connection Successful" : "Connection Failed",
        description: res.message,
        variant: res.success ? "default" : "destructive",
      });
    } catch (e: any) {
      toast({ title: "Error", description: e.message, variant: "destructive" });
    }
  };

  const handlePull = async () => {
    if (!pullCategories && !pullAttributes) {
      toast({ title: "Nothing selected", description: "Select at least one option.", variant: "destructive" });
      return;
    }
    try {
      const res = await pull.mutateAsync({ id: store.id, pullCategories, pullAttributes });
      const parts: string[] = [];
      if (res.synced_categories != null) parts.push(`${res.synced_categories} categories`);
      if (res.synced_attributes != null) parts.push(`${res.synced_attributes} attributes (${res.synced_terms ?? 0} terms)`);
      toast({
        title: "Pull Successful",
        description: `Pulled ${parts.join(", ")} from WooCommerce.`,
      });
      setLastPull({
        at: new Date(),
        categories: res.synced_categories,
        attributes: res.synced_attributes,
      });
      setPullOpen(false);
    } catch (e: any) {
      toast({ title: "Pull Failed", description: e.message, variant: "destructive" });
    }
  };

  return (
    <div className="bg-card border border-border/50 rounded-2xl shadow-lg shadow-black/5 flex flex-col hover:border-border transition-colors overflow-hidden">
      {/* Card header */}
      <div className="p-5">
        <div className="flex justify-between items-start mb-4">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl bg-secondary flex items-center justify-center border border-border">
              <StoreIcon className="w-5 h-5 text-foreground" />
            </div>
            <div>
              <h3 className="font-display font-semibold text-base text-foreground leading-tight">
                {store.name}
              </h3>
              <div className="flex items-center gap-1.5 mt-0.5 text-xs text-muted-foreground">
                <span
                  className={cn(
                    "w-1.5 h-1.5 rounded-full",
                    store.status === "active"
                      ? "bg-emerald-500"
                      : store.status === "error"
                      ? "bg-rose-500"
                      : "bg-slate-500"
                  )}
                />
                <span className="capitalize">{store.status}</span>
              </div>
            </div>
          </div>
          <button
            onClick={() => {
              if (confirm("Are you sure you want to delete this store?"))
                deleteStore.mutate({ id: store.id });
            }}
            className="p-1.5 text-muted-foreground hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>

        {/* Store details */}
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 text-xs text-muted-foreground bg-secondary/30 px-3 py-2 rounded-lg">
            <LinkIcon className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">{store.url}</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-1 rounded-lg text-xs">
            {store.wpUsername ? (
              <span className="flex items-center gap-1.5 text-emerald-400">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
                WP media upload enabled ({store.wpUsername})
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-amber-400">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />
                No WP credentials — image URL fallback
              </span>
            )}
          </div>
          {store.lastTestedAt && (
            <div className="text-xs text-muted-foreground px-1">
              Last tested: {format(new Date(store.lastTestedAt), "MMM d, yyyy")}
            </div>
          )}
        </div>

        {/* Last pull summary */}
        {lastPull && (
          <div className="mt-3 text-xs text-muted-foreground bg-emerald-500/5 border border-emerald-500/20 rounded-lg px-3 py-2 flex items-center justify-between gap-2">
            <span>
              Pulled{" "}
              {[
                lastPull.categories != null && `${lastPull.categories} categories`,
                lastPull.attributes != null && `${lastPull.attributes} attributes`,
              ]
                .filter(Boolean)
                .join(", ")}
            </span>
            <span className="text-muted-foreground/60">{format(lastPull.at, "h:mm a")}</span>
          </div>
        )}
      </div>

      {/* Pull from WooCommerce panel */}
      {pullOpen && (
        <div className="border-t border-border/50 bg-secondary/20 px-5 py-3.5 space-y-3">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Pull from WooCommerce
          </p>
          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-2.5 cursor-pointer group">
              <input
                type="checkbox"
                checked={pullCategories}
                onChange={(e) => setPullCategories(e.target.checked)}
                className="w-4 h-4 rounded accent-primary"
              />
              <Tag className="w-3.5 h-3.5 text-muted-foreground group-hover:text-foreground transition-colors" />
              <span className="text-sm">Categories</span>
            </label>
            <label className="flex items-center gap-2.5 cursor-pointer group">
              <input
                type="checkbox"
                checked={pullAttributes}
                onChange={(e) => setPullAttributes(e.target.checked)}
                className="w-4 h-4 rounded accent-primary"
              />
              <Layers className="w-3.5 h-3.5 text-muted-foreground group-hover:text-foreground transition-colors" />
              <span className="text-sm">Attributes &amp; Terms</span>
            </label>
          </div>
          <div className="flex gap-2 pt-1">
            <button
              onClick={handlePull}
              disabled={pull.isPending}
              className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              {pull.isPending ? (
                <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                <Download className="w-3.5 h-3.5" />
              )}
              {pull.isPending ? "Pulling…" : "Pull"}
            </button>
            <button
              onClick={() => setPullOpen(false)}
              className="px-3 py-2 rounded-lg bg-secondary hover:bg-secondary/80 text-xs font-medium transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Card actions */}
      <div className="mt-auto grid grid-cols-2 gap-2 px-5 pb-5 pt-3 border-t border-border/50">
        <button
          onClick={handleTest}
          disabled={testConn.isPending}
          className="px-3 py-2 rounded-xl bg-secondary hover:bg-secondary/80 text-foreground text-xs font-medium transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
        >
          {testConn.isPending ? (
            <div className="w-3.5 h-3.5 border-2 border-foreground border-t-transparent rounded-full animate-spin" />
          ) : (
            <AlertCircle className="w-3.5 h-3.5" />
          )}
          Test Connection
        </button>
        <button
          onClick={() => setPullOpen((v) => !v)}
          className={cn(
            "px-3 py-2 rounded-xl text-xs font-medium transition-colors flex items-center justify-center gap-1.5",
            pullOpen
              ? "bg-primary/20 text-primary"
              : "bg-primary/10 text-primary hover:bg-primary/20"
          )}
        >
          <Download className="w-3.5 h-3.5" />
          Pull from WooCommerce
          {pullOpen ? (
            <ChevronUp className="w-3 h-3 ml-0.5" />
          ) : (
            <ChevronDown className="w-3 h-3 ml-0.5" />
          )}
        </button>
      </div>
    </div>
  );
}

function AddStoreModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const createStore = useCreateStore();
  const [formData, setFormData] = useState({
    name: "",
    url: "",
    consumerKey: "",
    consumerSecret: "",
    wpUsername: "",
    wpAppPassword: "",
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const payload: Record<string, string> = {
      name: formData.name,
      url: formData.url,
      consumerKey: formData.consumerKey,
      consumerSecret: formData.consumerSecret,
    };
    if (formData.wpUsername.trim()) payload.wpUsername = formData.wpUsername.trim();
    if (formData.wpAppPassword.trim()) payload.wpAppPassword = formData.wpAppPassword.trim();

    createStore.mutate(
      { data: payload },
      {
        onSuccess: () => {
          onClose();
          setFormData({
            name: "",
            url: "",
            consumerKey: "",
            consumerSecret: "",
            wpUsername: "",
            wpAppPassword: "",
          });
        },
      }
    );
  };

  const inp =
    "w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all text-sm";

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Add WooCommerce Store">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Store Name</label>
          <input required type="text" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} className={inp} placeholder="My Dropship Store" />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Store URL</label>
          <input required type="url" value={formData.url} onChange={(e) => setFormData({ ...formData, url: e.target.value })} className={inp} placeholder="https://store.com" />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Consumer Key</label>
          <input required type="password" value={formData.consumerKey} onChange={(e) => setFormData({ ...formData, consumerKey: e.target.value })} className={inp} placeholder="ck_..." />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Consumer Secret</label>
          <input required type="password" value={formData.consumerSecret} onChange={(e) => setFormData({ ...formData, consumerSecret: e.target.value })} className={inp} placeholder="cs_..." />
        </div>

        <div className="pt-1 space-y-3 p-4 bg-secondary/20 rounded-xl border border-border/50">
          <div>
            <p className="text-xs font-medium text-foreground uppercase tracking-wide">
              WordPress Credentials (for image upload)
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Required to upload processed images to WordPress Media. WP Admin → Users → Profile → Application Passwords → Add New.
            </p>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">WP Username</label>
            <input type="text" value={formData.wpUsername} onChange={(e) => setFormData({ ...formData, wpUsername: e.target.value })} className={inp} placeholder="admin" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">WP Application Password</label>
            <input type="password" value={formData.wpAppPassword} onChange={(e) => setFormData({ ...formData, wpAppPassword: e.target.value })} className={inp} placeholder="xxxx xxxx xxxx xxxx xxxx xxxx" />
          </div>
        </div>

        <div className="pt-4 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="px-5 py-2.5 rounded-xl border border-border hover:bg-secondary font-medium text-sm">
            Cancel
          </button>
          <button type="submit" disabled={createStore.isPending} className="px-6 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all disabled:opacity-50 flex items-center gap-2 text-sm">
            {createStore.isPending ? "Saving…" : "Save Store"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
