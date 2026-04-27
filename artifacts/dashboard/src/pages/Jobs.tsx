import { useState } from "react";
import { useJobs, useCreateJob, useCancelJob } from "@/hooks/use-jobs";
import { useStores } from "@/hooks/use-stores";
import { StatusBadge } from "@/components/StatusBadge";
import { Modal } from "@/components/Modal";
import { Activity, Play, XCircle, ChevronRight, Link2, ArrowRightLeft } from "lucide-react";
import { format } from "date-fns";
import { CreateJobInputType, Job } from "@workspace/api-client-react";

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

      {/* Pipeline legend */}
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground bg-secondary/30 rounded-xl px-4 py-3 border border-border/40">
        <span className="font-medium text-foreground">Pipeline flow:</span>
        <StatusBadge status="fetch" />
        <ChevronRight className="w-3.5 h-3.5" />
        <StatusBadge status="process" />
        <ChevronRight className="w-3.5 h-3.5" />
        <StatusBadge status="upload" />
        <ChevronRight className="w-3.5 h-3.5" />
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-violet-500/15 text-violet-400 border border-violet-500/20 font-medium text-xs">
          <ArrowRightLeft className="w-3 h-3" /> sync
        </span>
        <span className="ml-1">— Use sync to push categories &amp; attributes to WooCommerce after uploading.</span>
      </div>

      <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-secondary/40 border-b border-border/50 text-sm text-muted-foreground">
                <th className="p-4 font-medium">Job ID</th>
                <th className="p-4 font-medium">Type</th>
                <th className="p-4 font-medium">Status</th>
                <th className="p-4 font-medium">Source Job</th>
                <th className="p-4 font-medium">Config / Filter</th>
                <th className="p-4 font-medium w-1/5">Progress</th>
                <th className="p-4 font-medium">Items</th>
                <th className="p-4 font-medium">Started</th>
                <th className="p-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {isLoading ? (
                <tr>
                  <td colSpan={9} className="p-8 text-center text-muted-foreground">Loading jobs…</td>
                </tr>
              ) : data?.jobs?.length === 0 ? (
                <tr>
                  <td colSpan={9} className="p-12 text-center text-muted-foreground">
                    <Activity className="w-12 h-12 mx-auto opacity-20 mb-3" />
                    No jobs recorded yet.
                  </td>
                </tr>
              ) : (
                data?.jobs?.map((job) => (
                  <tr key={job.id} className="hover:bg-secondary/20 transition-colors">
                    <td className="p-4 font-mono font-medium">#{job.id}</td>
                    <td className="p-4"><StatusBadge status={job.type} /></td>
                    <td className="p-4"><StatusBadge status={job.status} /></td>

                    {/* Source job link */}
                    <td className="p-4 text-sm">
                      {job.sourceJobId ? (
                        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-indigo-500/10 text-indigo-400 font-mono text-xs">
                          <Link2 className="w-3 h-3" />#{job.sourceJobId}
                        </span>
                      ) : (
                        <span className="text-muted-foreground text-xs">—</span>
                      )}
                    </td>

                    {/* Config summary */}
                    <td className="p-4 text-xs text-muted-foreground max-w-[180px]">
                      <ConfigSummary job={job} />
                    </td>

                    {/* Progress bar */}
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

                    <td className="p-4 text-sm font-medium tabular-nums">
                      {job.processedItems} / {job.totalItems}
                      {(job.failedItems ?? 0) > 0 && (
                        <span className="text-rose-400 ml-1">({job.failedItems} failed)</span>
                      )}
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
                        <span className="text-xs text-muted-foreground">
                          {job.completedAt ? format(new Date(job.completedAt), "HH:mm:ss") : '—'}
                        </span>
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
              Page <span className="font-medium text-foreground">{data.page}</span> of {data.totalPages}
              {" "}· {data.total} total jobs
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

/** Shows a compact summary of the job's config (filters used) */
function ConfigSummary({ job }: { job: Job }) {
  const cfg = (job.config ?? {}) as Record<string, unknown>;
  const parts: string[] = [];

  if (job.type === "fetch") {
    if (cfg.keyword) parts.push(`kw: "${cfg.keyword}"`);
    if (cfg.category_id) parts.push(`cat: ${cfg.category_id}`);
    if (cfg.page) parts.push(`p${cfg.page}`);
    if (cfg.limit) parts.push(`×${cfg.limit}`);
  } else if (job.type === "process" || job.type === "upload") {
    if (cfg.limit) parts.push(`limit: ${cfg.limit}`);
    if (job.type === "upload" && cfg.skip_images !== undefined) {
      parts.push(cfg.skip_images ? "no imgs" : "with imgs");
    }
  }

  if (parts.length === 0) return <span className="text-muted-foreground/50">—</span>;
  return <span className="font-mono text-xs">{parts.join(" · ")}</span>;
}


function NewJobModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [type, setType] = useState<CreateJobInputType>("fetch");
  const [storeId, setStoreId] = useState("");
  const [sourceJobId, setSourceJobId] = useState("");

  // Fetch-specific config
  const [fetchPage, setFetchPage] = useState("1");
  const [fetchLimit, setFetchLimit] = useState("50");
  const [fetchKeyword, setFetchKeyword] = useState("");
  const [fetchCategoryId, setFetchCategoryId] = useState("");

  // Upload config
  const [uploadLimit, setUploadLimit] = useState("50");
  const [skipImages, setSkipImages] = useState(true);

  // Sync config
  const [syncCategories, setSyncCategories] = useState(true);
  const [syncAttributes, setSyncAttributes] = useState(true);

  const createJob = useCreateJob();
  const { data: stores } = useStores();

  // Load all jobs to populate the source job dropdown — we filter by type below
  const { data: allJobsData } = useJobs({ page: 1, limit: 100 });
  const allJobs = allJobsData?.jobs ?? [];

  // For process jobs, pick from completed fetch jobs
  const fetchJobs = allJobs.filter(j => j.type === "fetch" && j.status === "completed");
  // For upload jobs, pick from completed process or fetch jobs
  const processableFetchJobs = allJobs.filter(
    j => (j.type === "fetch" || j.type === "process") && j.status === "completed"
  );
  // For sync jobs, pick from completed upload jobs
  const uploadJobs = allJobs.filter(j => j.type === "upload" && j.status === "completed");

  const handleTypeChange = (newType: CreateJobInputType) => {
    setType(newType);
    setStoreId("");
    setSourceJobId("");
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    let config: Record<string, unknown> = {};
    if (type === "fetch") {
      config = {
        page: parseInt(fetchPage) || 1,
        limit: parseInt(fetchLimit) || 50,
        ...(fetchKeyword.trim() ? { keyword: fetchKeyword.trim() } : {}),
        ...(fetchCategoryId.trim() ? { category_id: fetchCategoryId.trim() } : {}),
      };
    } else if (type === "process") {
      config = { limit: 50 };
    } else if (type === "upload") {
      config = {
        limit: parseInt(uploadLimit) || 50,
        skip_images: skipImages,
      };
    } else if (type === "sync") {
      config = {
        store_id: storeId ? parseInt(storeId) : undefined,
        sync_categories: syncCategories,
        sync_attributes: syncAttributes,
        limit: 200,
        ...(sourceJobId ? { source_job_id: parseInt(sourceJobId) } : {}),
      };
    }

    createJob.mutate({
      data: {
        type,
        storeId: storeId ? parseInt(storeId) : undefined,
        sourceJobId: sourceJobId ? parseInt(sourceJobId) : undefined,
        config,
      }
    }, {
      onSuccess: () => {
        onClose();
        setType("fetch");
        setStoreId("");
        setSourceJobId("");
        setFetchPage("1");
        setFetchKeyword("");
        setFetchCategoryId("");
      }
    });
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Start New Pipeline Job">
      <form onSubmit={handleSubmit} className="space-y-5">

        {/* Job Type */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-foreground block">Job Type</label>
          <select
            value={type}
            onChange={(e) => handleTypeChange(e.target.value as CreateJobInputType)}
            className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            required
          >
            <option value="fetch">Fetch — pull products from Sunsky</option>
            <option value="process">Process — compress & watermark images</option>
            <option value="upload">Upload — push to WooCommerce</option>
            <option value="sync">Sync — categories & attributes</option>
          </select>
        </div>

        {/* Source Job selector (process / upload) */}
        {(type === "process" || type === "upload") && (
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground block">
              Source Job <span className="text-muted-foreground font-normal">(scope to products from a specific fetch job)</span>
            </label>
            <select
              value={sourceJobId}
              onChange={(e) => setSourceJobId(e.target.value)}
              className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            >
              <option value="">— All eligible products (no filter) —</option>
              {(type === "process" ? fetchJobs : processableFetchJobs).map(j => (
                <option key={j.id} value={j.id}>
                  #{j.id} · {j.type.toUpperCase()} · {j.totalItems} items
                  {(j.config as any)?.keyword ? ` · kw: "${(j.config as any).keyword}"` : ""}
                  {(j.config as any)?.category_id ? ` · cat: ${(j.config as any).category_id}` : ""}
                </option>
              ))}
            </select>
            {sourceJobId && (
              <p className="text-xs text-indigo-400 flex items-center gap-1">
                <Link2 className="w-3 h-3" />
                This job will only operate on products fetched by job #{sourceJobId}.
              </p>
            )}
            {!sourceJobId && (
              <p className="text-xs text-muted-foreground">
                Leave blank to process/upload ALL eligible products regardless of which fetch job created them.
              </p>
            )}
          </div>
        )}

        {/* Sync-specific config */}
        {type === "sync" && (
          <div className="space-y-3 p-4 bg-secondary/20 rounded-xl border border-border/50">
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Sync Options</p>
            <label className="flex items-center gap-3 cursor-pointer">
              <input type="checkbox" checked={syncCategories} onChange={(e) => setSyncCategories(e.target.checked)} className="w-4 h-4 rounded accent-primary" />
              <span className="text-sm">Sync Categories <span className="text-muted-foreground text-xs">(create Sunsky categories in WooCommerce)</span></span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <input type="checkbox" checked={syncAttributes} onChange={(e) => setSyncAttributes(e.target.checked)} className="w-4 h-4 rounded accent-primary" />
              <span className="text-sm">Sync Attributes <span className="text-muted-foreground text-xs">(push product specs & variant options)</span></span>
            </label>
            <div className="space-y-1 pt-1">
              <label className="text-xs font-medium text-foreground">
                Scope to Upload Job <span className="text-muted-foreground font-normal">(optional)</span>
              </label>
              <select value={sourceJobId} onChange={(e) => setSourceJobId(e.target.value)} className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all">
                <option value="">— All uploaded products —</option>
                {uploadJobs.map(j => (
                  <option key={j.id} value={j.id}>#{j.id} · UPLOAD · {j.totalItems} products</option>
                ))}
              </select>
            </div>
          </div>
        )}

        {/* Fetch-specific config */}
        {type === "fetch" && (
          <div className="space-y-3 p-4 bg-secondary/20 rounded-xl border border-border/50">
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Fetch Options</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs font-medium text-foreground">Page Number</label>
                <input
                  type="number"
                  min="1"
                  value={fetchPage}
                  onChange={(e) => setFetchPage(e.target.value)}
                  placeholder="1"
                  className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-foreground">Limit (per page)</label>
                <input
                  type="number"
                  min="1"
                  max="200"
                  value={fetchLimit}
                  onChange={(e) => setFetchLimit(e.target.value)}
                  placeholder="50"
                  className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
                />
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-foreground">Keyword (optional)</label>
              <input
                type="text"
                value={fetchKeyword}
                onChange={(e) => setFetchKeyword(e.target.value)}
                placeholder="e.g. bluetooth earbuds"
                className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-foreground">Category ID (optional)</label>
              <input
                type="text"
                value={fetchCategoryId}
                onChange={(e) => setFetchCategoryId(e.target.value)}
                placeholder="e.g. 123 (leave blank for all)"
                className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Run multiple fetch jobs with different pages to import more products. Each job fetches up to {fetchLimit || 50} products.
            </p>
          </div>
        )}

        {/* Upload config */}
        {type === "upload" && (
          <div className="space-y-3 p-4 bg-secondary/20 rounded-xl border border-border/50">
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Upload Options</p>
            <div className="space-y-1">
              <label className="text-xs font-medium text-foreground">Max Products to Upload</label>
              <input
                type="number"
                min="1"
                max="200"
                value={uploadLimit}
                onChange={(e) => setUploadLimit(e.target.value)}
                placeholder="50"
                className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              />
            </div>
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={skipImages}
                onChange={(e) => setSkipImages(e.target.checked)}
                className="w-4 h-4 rounded accent-primary"
              />
              <span className="text-sm">
                Skip images <span className="text-muted-foreground text-xs">(recommended — WooCommerce often blocks CDN URLs)</span>
              </span>
            </label>
          </div>
        )}

        {/* Store selector for upload/sync */}
        {(type === "upload" || type === "sync") && (
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground block">Target WooCommerce Store</label>
            <select
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              className="w-full bg-background border border-border rounded-xl px-4 py-3 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              required
            >
              <option value="">Select a store…</option>
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
            {createJob.isPending
              ? "Starting…"
              : <><Play className="w-4 h-4 fill-current" /> Start Job</>}
          </button>
        </div>
      </form>
    </Modal>
  );
}
