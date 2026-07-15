import { useState } from "react";
import { useStores, useCreateStore, useDeleteStore, useTestConnection, useSyncCategories } from "@/hooks/use-stores";
import { Modal } from "@/components/Modal";
import { Store as StoreIcon, Plus, Trash2, RefreshCw, Link as LinkIcon, AlertCircle } from "lucide-react";
import { format } from "date-fns";
import { useToast } from "@/hooks/use-toast";

export default function Stores() {
  const { data: stores, isLoading } = useStores();
  const [isModalOpen, setIsModalOpen] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between gap-4 items-start sm:items-center">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground">Stores</h1>
          <p className="text-muted-foreground mt-1">Manage connected WooCommerce stores.</p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="px-5 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2"
        >
          <Plus className="w-5 h-5" /> Add Store
        </button>
      </div>

      {isLoading ? (
        <div className="py-20 flex justify-center"><div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin"></div></div>
      ) : stores?.length === 0 ? (
        <div className="bg-card border border-border/50 rounded-2xl p-12 text-center text-muted-foreground shadow-lg shadow-black/5">
          <StoreIcon className="w-16 h-16 mx-auto opacity-20 mb-4" />
          <h3 className="text-lg font-medium text-foreground mb-1">No stores configured</h3>
          <p>Add your first WooCommerce store to start syncing products.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          {stores?.map(store => (
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
  const syncCats = useSyncCategories();
  const deleteStore = useDeleteStore();
  const { toast } = useToast();

  const handleTest = async () => {
    try {
      const res = await testConn.mutateAsync({ id: store.id });
      toast({
        title: res.success ? "Connection Successful" : "Connection Failed",
        description: res.message,
        variant: res.success ? "default" : "destructive"
      });
    } catch (e: any) {
      toast({ title: "Error", description: e.message, variant: "destructive" });
    }
  };

  const handleSync = async () => {
    try {
      const res = await syncCats.mutateAsync({ id: store.id });
      toast({
        title: "Sync Started",
        description: `Synced ${res.synced} categories (${res.created} new, ${res.updated} updated).`,
      });
    } catch (e: any) {
      toast({ title: "Error syncing", description: e.message, variant: "destructive" });
    }
  };

  return (
    <div className="bg-card border border-border/50 rounded-2xl p-6 shadow-lg shadow-black/5 flex flex-col hover:border-border transition-colors">
      <div className="flex justify-between items-start mb-4">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-secondary flex items-center justify-center border border-border">
            <StoreIcon className="w-6 h-6 text-foreground" />
          </div>
          <div>
            <h3 className="font-display font-semibold text-lg text-foreground leading-tight">{store.name}</h3>
            <div className="flex items-center gap-1.5 mt-1 text-sm text-muted-foreground">
              <div className={`w-2 h-2 rounded-full ${store.status === 'active' ? 'bg-emerald-500' : store.status === 'error' ? 'bg-rose-500' : 'bg-slate-500'}`} />
              <span className="capitalize">{store.status}</span>
            </div>
          </div>
        </div>
        <button 
          onClick={() => {
            if(confirm("Are you sure you want to delete this store?")) deleteStore.mutate({ id: store.id });
          }}
          className="p-2 text-muted-foreground hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>

      <div className="space-y-2 mb-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground bg-secondary/30 px-3 py-2 rounded-lg">
          <LinkIcon className="w-4 h-4" />
          <span className="truncate">{store.url}</span>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs">
          {store.wpUsername ? (
            <span className="flex items-center gap-1.5 text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
              WP media upload enabled ({store.wpUsername})
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-amber-400">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />
              No WP credentials — images use static URL fallback
            </span>
          )}
        </div>
        {store.lastTestedAt && (
          <div className="text-xs text-muted-foreground px-1">
            Tested: {format(new Date(store.lastTestedAt), "MMM d, yyyy")}
          </div>
        )}
      </div>

      <div className="mt-auto grid grid-cols-2 gap-3 pt-4 border-t border-border/50">
        <button 
          onClick={handleTest}
          disabled={testConn.isPending}
          className="px-4 py-2 rounded-xl bg-secondary hover:bg-secondary/80 text-foreground text-sm font-medium transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {testConn.isPending ? <div className="w-4 h-4 border-2 border-foreground border-t-transparent rounded-full animate-spin"/> : <AlertCircle className="w-4 h-4" />}
          Test
        </button>
        <button 
          onClick={handleSync}
          disabled={syncCats.isPending}
          className="px-4 py-2 rounded-xl bg-primary/10 text-primary hover:bg-primary/20 text-sm font-medium transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {syncCats.isPending ? <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin"/> : <RefreshCw className="w-4 h-4" />}
          Sync
        </button>
      </div>
    </div>
  );
}

function AddStoreModal({ isOpen, onClose }: { isOpen: boolean, onClose: () => void }) {
  const createStore = useCreateStore();
  const [formData, setFormData] = useState({
    name: '', url: '', consumerKey: '', consumerSecret: '',
    wpUsername: '', wpAppPassword: '',
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

    createStore.mutate({ data: payload }, {
      onSuccess: () => {
        onClose();
        setFormData({ name: '', url: '', consumerKey: '', consumerSecret: '', wpUsername: '', wpAppPassword: '' });
      }
    });
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Add WooCommerce Store">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Store Name</label>
          <input required type="text" value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})} className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all" placeholder="My Dropship Store" />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Store URL</label>
          <input required type="url" value={formData.url} onChange={e => setFormData({...formData, url: e.target.value})} className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all" placeholder="https://store.com" />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Consumer Key</label>
          <input required type="password" value={formData.consumerKey} onChange={e => setFormData({...formData, consumerKey: e.target.value})} className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all" placeholder="ck_..." />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Consumer Secret</label>
          <input required type="password" value={formData.consumerSecret} onChange={e => setFormData({...formData, consumerSecret: e.target.value})} className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all" placeholder="cs_..." />
        </div>

        {/* WordPress Application Password — for image upload */}
        <div className="pt-1 space-y-3 p-4 bg-secondary/20 rounded-xl border border-border/50">
          <div>
            <p className="text-xs font-medium text-foreground uppercase tracking-wide">WordPress Credentials (for image upload)</p>
            <p className="text-xs text-muted-foreground mt-1">
              Required to upload processed images directly to WordPress media.
              WP Admin → Users → Profile → Application Passwords → Add New.
            </p>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">WP Username</label>
            <input type="text" value={formData.wpUsername} onChange={e => setFormData({...formData, wpUsername: e.target.value})} className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all" placeholder="admin" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">WP Application Password</label>
            <input type="password" value={formData.wpAppPassword} onChange={e => setFormData({...formData, wpAppPassword: e.target.value})} className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 transition-all" placeholder="xxxx xxxx xxxx xxxx xxxx xxxx" />
          </div>
        </div>

        <div className="pt-4 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="px-5 py-2.5 rounded-xl border border-border hover:bg-secondary font-medium">Cancel</button>
          <button type="submit" disabled={createStore.isPending} className="px-6 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all disabled:opacity-50 flex items-center gap-2">
            {createStore.isPending ? "Saving..." : "Save Store"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
