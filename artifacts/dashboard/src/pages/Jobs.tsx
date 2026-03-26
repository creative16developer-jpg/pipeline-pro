import { useState } from "react";
import { useJobs, useCreateJob, useCancelJob } from "@/hooks/use-jobs";
import { useStores } from "@/hooks/use-stores";
import { StatusBadge } from "@/components/StatusBadge";
import { Modal } from "@/components/Modal";
import { Activity, Play, XCircle, MoreVertical } from "lucide-react";
import { format } from "date-fns";
import { CreateJobInputType } from "@workspace/api-client-react";

export default function Jobs() {
  const [page, setPage] = useState(1);
  const [isNewJobModalOpen, setIsNewJobModalOpen] = useState(false);
  const { data, isLoading } = useJobs({ page, limit: 15 });
  const cancelJob = useCancelJob();

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between gap-4 items-start sm:items-center">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground">Import Jobs</h1>
          <p className="text-muted-foreground mt-1">Manage and monitor pipeline execution tasks.</p>
        </div>
        <button
          onClick={() => setIsNewJobModalOpen(true)}
          className="px-5 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2"
        >
          <Play className="w-4 h-4 fill-current" /> Start New Job
        </button>
      </div>

      <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-secondary/40 border-b border-border/50 text-sm text-muted-foreground">
                <th className="p-4 font-medium">Job ID</th>
                <th className="p-4 font-medium">Type</th>
                <th className="p-4 font-medium">Status</th>
                <th className="p-4 font-medium w-1/4">Progress</th>
                <th className="p-4 font-medium">Items (Done/Total)</th>
                <th className="p-4 font-medium">Started At</th>
                <th className="p-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {isLoading ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-muted-foreground">Loading jobs...</td>
                </tr>
              ) : data?.jobs?.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-12 text-center text-muted-foreground">
                    <Activity className="w-12 h-12 mx-auto opacity-20 mb-3" />
                    No jobs recorded yet.
                  </td>
                </tr>
              ) : (
                data?.jobs?.map((job) => (
                  <tr key={job.id} className="hover:bg-secondary/20 transition-colors">
                    <td className="p-4 font-medium">#{job.id}</td>
                    <td className="p-4"><StatusBadge status={job.type} /></td>
                    <td className="p-4"><StatusBadge status={job.status} /></td>
                    <td className="p-4">
                      <div className="flex items-center gap-3">
                        <div className="flex-1 h-2 bg-secondary rounded-full overflow-hidden">
                          <div 
                            className={`h-full rounded-full ${job.status === 'failed' ? 'bg-rose-500' : 'bg-primary'} transition-all duration-500`} 
                            style={{ width: `${job.progressPercent}%` }}
                          />
                        </div>
                        <span className="text-xs font-medium w-9 text-right">{job.progressPercent}%</span>
                      </div>
                      {job.errorMessage && (
                        <p className="text-xs text-rose-400 mt-1 line-clamp-1" title={job.errorMessage}>{job.errorMessage}</p>
                      )}
                    </td>
                    <td className="p-4 text-sm font-medium">
                      {job.processedItems} / {job.totalItems}
                    </td>
                    <td className="p-4 text-sm text-muted-foreground">
                      {job.startedAt ? format(new Date(job.startedAt), "MMM d, HH:mm:ss") : '—'}
                    </td>
                    <td className="p-4 text-right">
                      {job.status === 'running' || job.status === 'pending' ? (
                        <button
                          onClick={() => cancelJob.mutate({ id: job.id })}
                          disabled={cancelJob.isPending}
                          className="px-3 py-1.5 rounded-lg bg-rose-500/10 text-rose-400 hover:bg-rose-500/20 text-sm font-medium transition-colors disabled:opacity-50"
                        >
                          Cancel
                        </button>
                      ) : (
                        <button className="p-1.5 rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors">
                          <MoreVertical className="w-5 h-5" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        
        {/* Pagination */}
        {data && data.totalPages > 1 && (
          <div className="p-4 border-t border-border/50 bg-secondary/20 flex items-center justify-between">
            <span className="text-sm text-muted-foreground">
              Showing page <span className="font-medium text-foreground">{data.page}</span> of {data.totalPages}
            </span>
            <div className="flex gap-2">
              <button 
                disabled={page <= 1} 
                onClick={() => setPage(p => p - 1)}
                className="px-4 py-2 rounded-xl border border-border bg-background hover:bg-secondary transition-colors disabled:opacity-50 text-sm font-medium"
              >
                Previous
              </button>
              <button 
                disabled={page >= data.totalPages} 
                onClick={() => setPage(p => p + 1)}
                className="px-4 py-2 rounded-xl border border-border bg-background hover:bg-secondary transition-colors disabled:opacity-50 text-sm font-medium"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      <NewJobModal isOpen={isNewJobModalOpen} onClose={() => setIsNewJobModalOpen(false)} />
    </div>
  );
}

function NewJobModal({ isOpen, onClose }: { isOpen: boolean, onClose: () => void }) {
  const [type, setType] = useState<CreateJobInputType>("process");
  const [storeId, setStoreId] = useState("");
  const createJob = useCreateJob();
  const { data: stores } = useStores();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createJob.mutate({
      data: {
        type,
        storeId: storeId ? parseInt(storeId) : undefined,
        config: {}
      }
    }, {
      onSuccess: () => {
        onClose();
        setType("process");
        setStoreId("");
      }
    });
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Start New Pipeline Job">
      <form onSubmit={handleSubmit} className="space-y-5">
        <div className="space-y-2">
          <label className="text-sm font-medium text-foreground block">Job Type</label>
          <select
            value={type}
            onChange={(e) => setType(e.target.value as CreateJobInputType)}
            className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            required
          >
            <option value="fetch">Fetch (from Sunsky)</option>
            <option value="process">Process (Watermark & AI)</option>
            <option value="upload">Upload (to WooCommerce)</option>
            <option value="sync">Sync (Categories/Attributes)</option>
          </select>
        </div>

        {(type === "upload" || type === "sync") && (
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground block">Target WooCommerce Store</label>
            <select
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              required
            >
              <option value="">Select a store...</option>
              {stores?.map(store => (
                <option key={store.id} value={store.id}>{store.name}</option>
              ))}
            </select>
          </div>
        )}

        <div className="pt-4 border-t border-border/50 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-5 py-2.5 rounded-xl border border-border hover:bg-secondary transition-colors font-medium"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={createJob.isPending}
            className="px-6 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium shadow-lg shadow-primary/25 hover:shadow-xl hover:shadow-primary/40 hover:-translate-y-0.5 transition-all disabled:opacity-50 disabled:transform-none flex items-center gap-2"
          >
            {createJob.isPending ? "Starting..." : <><Play className="w-4 h-4 fill-current" /> Start Job</>}
          </button>
        </div>
      </form>
    </Modal>
  );
}
