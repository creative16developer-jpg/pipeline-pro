import { useState } from "react";
import { useProducts, useProduct } from "@/hooks/use-products";
import { StatusBadge } from "@/components/StatusBadge";
import { Modal } from "@/components/Modal";
import { Search, Package, Image as ImageIcon, ExternalLink, Filter } from "lucide-react";
import { format } from "date-fns";

export default function Products() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<any>("");
  const [selectedProductId, setSelectedProductId] = useState<number | null>(null);

  const { data, isLoading } = useProducts({ 
    page, 
    limit: 20, 
    search: search || undefined, 
    status: status || undefined 
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between gap-4 items-start sm:items-center">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground">Product Catalog</h1>
          <p className="text-muted-foreground mt-1">Manage fetched and processed products.</p>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-card border border-border/50 p-4 rounded-2xl flex flex-col sm:flex-row gap-4 shadow-lg shadow-black/5">
        <div className="relative flex-1">
          <Search className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search by SKU or Name..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="w-full bg-background border border-border rounded-xl pl-10 pr-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-all text-sm"
          />
        </div>
        <div className="relative w-full sm:w-64">
          <Filter className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <select
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1); }}
            className="w-full bg-background border border-border rounded-xl pl-10 pr-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-all text-sm appearance-none"
          >
            <option value="">All Statuses</option>
            <option value="pending">Pending</option>
            <option value="processing">Processing</option>
            <option value="processed">Processed</option>
            <option value="uploaded">Uploaded</option>
            <option value="failed">Failed</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-lg shadow-black/5">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-secondary/40 border-b border-border/50 text-sm text-muted-foreground">
                <th className="p-4 font-medium">SKU</th>
                <th className="p-4 font-medium">Product</th>
                <th className="p-4 font-medium">Category</th>
                <th className="p-4 font-medium">Price</th>
                <th className="p-4 font-medium">Status</th>
                <th className="p-4 font-medium">Images</th>
                <th className="p-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {isLoading ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-muted-foreground">Loading products...</td>
                </tr>
              ) : data?.products?.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-12 text-center text-muted-foreground">
                    <Package className="w-12 h-12 mx-auto opacity-20 mb-3" />
                    No products found matching your criteria.
                  </td>
                </tr>
              ) : (
                data?.products?.map((product) => (
                  <tr key={product.id} className="hover:bg-secondary/20 transition-colors">
                    <td className="p-4 font-mono text-sm">{product.sku}</td>
                    <td className="p-4">
                      <div className="font-medium text-foreground max-w-xs truncate">{product.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">Sunsky ID: {product.sunskyId}</div>
                    </td>
                    <td className="p-4 text-sm">{product.categoryId || '—'}</td>
                    <td className="p-4 text-sm font-medium">{product.price ? `$${product.price}` : '—'}</td>
                    <td className="p-4"><StatusBadge status={product.status} /></td>
                    <td className="p-4">
                      <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                        <ImageIcon className="w-4 h-4" /> {product.imageCount}
                      </div>
                    </td>
                    <td className="p-4 text-right">
                      <button
                        onClick={() => setSelectedProductId(product.id)}
                        className="px-3 py-1.5 rounded-lg bg-secondary hover:bg-secondary/80 text-sm font-medium transition-colors"
                      >
                        Details
                      </button>
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
                className="px-4 py-2 rounded-xl border border-border bg-background hover:bg-secondary transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
              >
                Previous
              </button>
              <button 
                disabled={page >= data.totalPages} 
                onClick={() => setPage(p => p + 1)}
                className="px-4 py-2 rounded-xl border border-border bg-background hover:bg-secondary transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      <ProductDetailModal 
        id={selectedProductId} 
        onClose={() => setSelectedProductId(null)} 
      />
    </div>
  );
}

function ProductDetailModal({ id, onClose }: { id: number | null, onClose: () => void }) {
  const { data: product, isLoading } = useProduct(id as number);

  return (
    <Modal isOpen={!!id} onClose={onClose} title={product?.name || "Product Details"} className="max-w-3xl">
      {isLoading ? (
        <div className="py-12 flex justify-center"><div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin"></div></div>
      ) : product ? (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-secondary/30 p-4 rounded-xl border border-border/50">
              <span className="text-xs text-muted-foreground block mb-1">SKU</span>
              <span className="font-mono font-medium">{product.sku}</span>
            </div>
            <div className="bg-secondary/30 p-4 rounded-xl border border-border/50">
              <span className="text-xs text-muted-foreground block mb-1">Status</span>
              <StatusBadge status={product.status} />
            </div>
            <div className="bg-secondary/30 p-4 rounded-xl border border-border/50">
              <span className="text-xs text-muted-foreground block mb-1">Price</span>
              <span className="font-medium">{product.price ? `$${product.price}` : 'Unknown'}</span>
            </div>
            <div className="bg-secondary/30 p-4 rounded-xl border border-border/50">
              <span className="text-xs text-muted-foreground block mb-1">WooCommerce ID</span>
              <span className="font-medium flex items-center gap-2">
                {product.wooProductId || 'Not uploaded'}
                {product.wooProductId && <ExternalLink className="w-4 h-4 text-muted-foreground" />}
              </span>
            </div>
          </div>

          {product.errorMessage && (
            <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-sm">
              <p className="font-bold mb-1">Error Message</p>
              {product.errorMessage}
            </div>
          )}

          <div>
            <h3 className="text-lg font-display font-medium mb-3">Raw Data</h3>
            <div className="bg-black/40 border border-border rounded-xl p-4 overflow-x-auto">
              <pre className="text-xs font-mono text-muted-foreground">
                {JSON.stringify(product.rawData || {}, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      ) : (
        <div className="py-8 text-center text-muted-foreground">Failed to load product.</div>
      )}
    </Modal>
  );
}
