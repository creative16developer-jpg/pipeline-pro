import { useState, useEffect, useCallback } from "react";
import {
  Sparkles, Settings2, Play, Eye, ChevronRight, CheckCircle2,
  XCircle, Loader2, RotateCcw, Save, Copy, X, Info, Zap
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const FIELD_LIST = [
  "description",
  "short_description",
  "slug",
  "meta_title",
  "meta_description",
  "tags",
  "image_alt",
  "image_names",
];

const FIELD_LABELS: Record<string, string> = {
  description: "Description",
  short_description: "Short Description",
  slug: "URL Slug",
  meta_title: "Meta Title",
  meta_description: "Meta Description",
  tags: "Tags",
  image_alt: "Image Alt Text",
  image_names: "Image File Names",
};

const MODE_OPTIONS = ["logic", "ai", "hybrid"] as const;
type Mode = (typeof MODE_OPTIONS)[number];

const STRUCTURE_OPTIONS = ["intro", "features", "benefits", "compatibility"];

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface FieldOptions {
  structure?: string[];
  keyword_source?: string;
  max_words?: number;
  max_chars?: number;
  transliterate?: boolean;
  ensure_unique?: boolean;
  max_tags?: number;
  include_specs?: boolean;
  include_sku?: boolean;
  pattern?: string;
}

interface FieldConfig {
  enabled: boolean;
  mode: Mode;
  options: FieldOptions;
}

interface GlobalSettings {
  ai_enabled: boolean;
  max_calls_per_product: number;
  keyword_strategy: string;
  fallback_strategy: string;
}

interface GenerateConfig {
  globalSettings: GlobalSettings;
  fields: Record<string, FieldConfig>;
  overrides: Record<string, string>;
}

interface FieldResult {
  field: string;
  value: string;
  source: string;
  status: "ok" | "failed" | "skipped";
  error?: string;
}

interface GenerationJob {
  taskId: string;
  status: string;
  totalFields: number;
  doneFields: number;
  fields: Record<string, FieldResult>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Default config
// ─────────────────────────────────────────────────────────────────────────────

const DEFAULT_CONFIG: GenerateConfig = {
  globalSettings: {
    ai_enabled: false,
    max_calls_per_product: 3,
    keyword_strategy: "auto",
    fallback_strategy: "safe",
  },
  fields: Object.fromEntries(
    FIELD_LIST.map((f) => [
      f,
      {
        enabled: true,
        mode: "logic" as Mode,
        options:
          f === "description"
            ? { structure: ["intro", "features", "benefits", "compatibility"], keyword_source: "auto" }
            : f === "slug"
            ? { transliterate: true, ensure_unique: true }
            : f === "meta_title"
            ? { max_chars: 60 }
            : f === "meta_description"
            ? { max_chars: 155 }
            : f === "tags"
            ? { max_tags: 8, include_specs: true }
            : f === "image_alt"
            ? { include_sku: true }
            : f === "image_names"
            ? { pattern: "{sku}-{name}" }
            : f === "short_description"
            ? { max_words: 30 }
            : {},
      },
    ])
  ),
  overrides: {},
};

// ─────────────────────────────────────────────────────────────────────────────
// Helper: CSS classes
// ─────────────────────────────────────────────────────────────────────────────

const inputCls =
  "w-full bg-background border border-border rounded-xl px-4 py-2.5 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all text-sm";

const toggleCls = (on: boolean) =>
  cn(
    "relative inline-flex h-5 w-9 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none",
    on ? "bg-primary" : "bg-secondary"
  );

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={toggleCls(checked)}
    >
      <span
        className={cn(
          "pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200",
          checked ? "translate-x-4" : "translate-x-0"
        )}
      />
    </button>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// FieldConfigPanel  (slide-over for per-field settings)
// ─────────────────────────────────────────────────────────────────────────────

function FieldConfigPanel({
  field,
  config,
  onChange,
  onClose,
}: {
  field: string;
  config: FieldConfig;
  onChange: (cfg: FieldConfig) => void;
  onClose: () => void;
}) {
  const label = FIELD_LABELS[field] ?? field;
  const opt = config.options;

  const setOpt = (patch: Partial<FieldOptions>) =>
    onChange({ ...config, options: { ...opt, ...patch } });

  const toggleStructure = (item: string) => {
    const cur = opt.structure ?? [];
    setOpt({ structure: cur.includes(item) ? cur.filter((x) => x !== item) : [...cur, item] });
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-md bg-card border-l border-border shadow-2xl flex flex-col h-full animate-in slide-in-from-right duration-300">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-border/50">
          <div>
            <h3 className="font-semibold text-foreground">{label}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">Configure generation options</p>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-secondary text-muted-foreground">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {/* Mode */}
          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Generation Mode</label>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {MODE_OPTIONS.map((m) => (
                <button
                  key={m}
                  onClick={() => onChange({ ...config, mode: m })}
                  className={cn(
                    "px-3 py-2 rounded-lg text-sm font-medium border transition-all capitalize",
                    config.mode === m
                      ? "bg-primary/10 border-primary/40 text-primary"
                      : "border-border text-muted-foreground hover:text-foreground hover:border-border/80"
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>

          {/* Field-specific options */}
          {field === "description" && (
            <>
              <div>
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Content Structure</label>
                <div className="mt-2 space-y-2">
                  {STRUCTURE_OPTIONS.map((item) => (
                    <label key={item} className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={(opt.structure ?? []).includes(item)}
                        onChange={() => toggleStructure(item)}
                        className="w-4 h-4 rounded border-border accent-primary"
                      />
                      <span className="text-sm capitalize">{item}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Keyword Source</label>
                <select
                  value={opt.keyword_source ?? "auto"}
                  onChange={(e) => setOpt({ keyword_source: e.target.value })}
                  className={cn(inputCls, "mt-2")}
                >
                  <option value="auto">Auto</option>
                  <option value="specs">From Specs</option>
                  <option value="name">From Name</option>
                  <option value="none">None</option>
                </select>
              </div>
            </>
          )}

          {field === "short_description" && (
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Max Words</label>
              <input
                type="number"
                value={opt.max_words ?? 30}
                min={5}
                max={100}
                onChange={(e) => setOpt({ max_words: Number(e.target.value) })}
                className={cn(inputCls, "mt-2")}
              />
            </div>
          )}

          {field === "slug" && (
            <>
              <div className="flex items-center justify-between">
                <label className="text-sm">Transliterate</label>
                <Toggle checked={!!opt.transliterate} onChange={(v) => setOpt({ transliterate: v })} />
              </div>
              <div className="flex items-center justify-between">
                <label className="text-sm">Ensure Unique (append SKU)</label>
                <Toggle checked={!!opt.ensure_unique} onChange={(v) => setOpt({ ensure_unique: v })} />
              </div>
            </>
          )}

          {(field === "meta_title" || field === "meta_description") && (
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Max Characters
              </label>
              <input
                type="number"
                value={opt.max_chars ?? (field === "meta_title" ? 60 : 155)}
                min={20}
                max={300}
                onChange={(e) => setOpt({ max_chars: Number(e.target.value) })}
                className={cn(inputCls, "mt-2")}
              />
            </div>
          )}

          {field === "tags" && (
            <>
              <div>
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Max Tags</label>
                <input
                  type="number"
                  value={opt.max_tags ?? 8}
                  min={1}
                  max={20}
                  onChange={(e) => setOpt({ max_tags: Number(e.target.value) })}
                  className={cn(inputCls, "mt-2")}
                />
              </div>
              <div className="flex items-center justify-between">
                <label className="text-sm">Include spec values</label>
                <Toggle checked={!!opt.include_specs} onChange={(v) => setOpt({ include_specs: v })} />
              </div>
            </>
          )}

          {field === "image_alt" && (
            <div className="flex items-center justify-between">
              <label className="text-sm">Include SKU in alt text</label>
              <Toggle checked={!!opt.include_sku} onChange={(v) => setOpt({ include_sku: v })} />
            </div>
          )}

          {field === "image_names" && (
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Filename Pattern</label>
              <input
                type="text"
                value={opt.pattern ?? "{sku}-{name}"}
                onChange={(e) => setOpt({ pattern: e.target.value })}
                className={cn(inputCls, "mt-2")}
                placeholder="{sku}-{name}"
              />
              <p className="text-xs text-muted-foreground mt-1">Variables: {"{sku}"}, {"{name}"}</p>
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-border/50">
          <button
            onClick={onClose}
            className="w-full py-2.5 rounded-xl bg-primary text-primary-foreground font-medium text-sm hover:bg-primary/90 transition-colors"
          >
            Save & Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// PreviewPanel
// ─────────────────────────────────────────────────────────────────────────────

function PreviewPanel({
  field,
  result,
  override,
  onOverride,
  onClearOverride,
}: {
  field: string | null;
  result: FieldResult | null;
  override: string;
  onOverride: (v: string) => void;
  onClearOverride: () => void;
}) {
  const [copied, setCopied] = useState(false);

  if (!field || !result) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center py-16 text-muted-foreground">
        <Eye className="w-10 h-10 mb-3 opacity-30" />
        <p className="text-sm">Click Preview on any field to see generated content here</p>
      </div>
    );
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(result.value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const isHtml = field === "description" || field === "short_description";

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm">{FIELD_LABELS[field] ?? field}</span>
          <span
            className={cn(
              "text-xs px-2 py-0.5 rounded-full border",
              result.status === "ok"
                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                : result.status === "failed"
                ? "bg-red-500/10 text-red-400 border-red-500/20"
                : "bg-secondary text-muted-foreground border-border"
            )}
          >
            {result.source}
          </span>
        </div>
        <button onClick={handleCopy} className="p-1.5 rounded-lg hover:bg-secondary text-muted-foreground transition-colors">
          <Copy className="w-3.5 h-3.5" />
          {copied && <span className="ml-1 text-xs">Copied!</span>}
        </button>
      </div>

      {/* Generated output */}
      {result.status === "failed" ? (
        <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          {result.error ?? "Generation failed"}
        </div>
      ) : isHtml ? (
        <div
          className="p-4 rounded-xl bg-secondary/40 border border-border/50 text-sm leading-relaxed prose prose-invert max-w-none"
          dangerouslySetInnerHTML={{ __html: result.value || "<em>empty</em>" }}
        />
      ) : (
        <div className="p-4 rounded-xl bg-secondary/40 border border-border/50 text-sm font-mono break-all">
          {result.value || <span className="text-muted-foreground italic">empty</span>}
        </div>
      )}

      {/* Manual override */}
      <div>
        <label className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
          Manual Override
        </label>
        <textarea
          value={override}
          onChange={(e) => onOverride(e.target.value)}
          rows={3}
          placeholder="Type a custom value to override the generated content…"
          className={cn(inputCls, "mt-1.5 resize-none")}
        />
        {override && (
          <button
            onClick={onClearOverride}
            className="mt-1.5 text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            <RotateCcw className="w-3 h-3" /> Clear override
          </button>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Result Row (inside job results panel)
// ─────────────────────────────────────────────────────────────────────────────

function ResultRow({ result }: { result: FieldResult }) {
  const [expanded, setExpanded] = useState(false);
  const label = FIELD_LABELS[result.field] ?? result.field;

  return (
    <div className="border border-border/50 rounded-xl overflow-hidden">
      <button
        onClick={() => setExpanded((x) => !x)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-secondary/30 transition-colors text-left"
      >
        <div className="flex items-center gap-3 min-w-0">
          {result.status === "ok" ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          ) : result.status === "failed" ? (
            <XCircle className="w-4 h-4 text-red-400 shrink-0" />
          ) : (
            <div className="w-4 h-4 rounded-full border border-border shrink-0" />
          )}
          <span className="font-medium text-sm">{label}</span>
          <span className="text-xs text-muted-foreground px-2 py-0.5 rounded-full bg-secondary border border-border/50">
            {result.source}
          </span>
        </div>
        <ChevronRight className={cn("w-4 h-4 text-muted-foreground transition-transform", expanded && "rotate-90")} />
      </button>
      {expanded && (
        <div className="px-4 pb-4 border-t border-border/30 pt-3">
          {result.status === "failed" ? (
            <p className="text-sm text-red-400">{result.error}</p>
          ) : (
            <p className="text-sm text-muted-foreground break-all whitespace-pre-wrap font-mono text-xs">
              {result.value || <span className="italic">empty</span>}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Page
// ─────────────────────────────────────────────────────────────────────────────

export default function ContentGeneration() {
  const { toast } = useToast();

  // Config state
  const [config, setConfig] = useState<GenerateConfig>(DEFAULT_CONFIG);

  // Product picker
  const [products, setProducts] = useState<any[]>([]);
  const [selectedProduct, setSelectedProduct] = useState<any | null>(null);
  const [loadingProducts, setLoadingProducts] = useState(false);

  // Field config panel
  const [panelField, setPanelField] = useState<string | null>(null);

  // Preview
  const [previewingField, setPreviewingField] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<FieldResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewOverride, setPreviewOverride] = useState("");

  // Generation
  const [running, setRunning] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);

  // ── Load products for picker ─────────────────────────────────────────────
  useEffect(() => {
    setLoadingProducts(true);
    fetch("/api/products?limit=50&status=uploaded")
      .then((r) => r.json())
      .then((d) => {
        const list = d.products ?? d ?? [];
        setProducts(list);
        if (list.length > 0 && !selectedProduct) {
          setSelectedProduct(list[0]);
        }
      })
      .catch(() => {
        // try without filter
        fetch("/api/products?limit=50")
          .then((r) => r.json())
          .then((d) => {
            const list = d.products ?? d ?? [];
            setProducts(list);
            if (list.length > 0) setSelectedProduct(list[0]);
          })
          .catch(() => {});
      })
      .finally(() => setLoadingProducts(false));
  }, []);

  // ── Config patch helpers ────────────────────────────────────────────────
  const patchField = useCallback((field: string, patch: Partial<FieldConfig>) => {
    setConfig((c) => ({
      ...c,
      fields: { ...c.fields, [field]: { ...c.fields[field], ...patch } },
    }));
  }, []);

  const patchGlobal = useCallback((patch: Partial<GlobalSettings>) => {
    setConfig((c) => ({ ...c, globalSettings: { ...c.globalSettings, ...patch } }));
  }, []);

  const setOverride = useCallback((field: string, value: string) => {
    setConfig((c) => ({
      ...c,
      overrides: value ? { ...c.overrides, [field]: value } : (() => {
        const o = { ...c.overrides };
        delete o[field];
        return o;
      })(),
    }));
  }, []);

  // ── Preview single field ────────────────────────────────────────────────
  const handlePreview = async (field: string) => {
    if (!selectedProduct) {
      toast({ title: "No product selected", description: "Pick a product first.", variant: "destructive" });
      return;
    }
    setPreviewingField(field);
    setPreviewLoading(true);
    setPreviewResult(null);
    setPreviewOverride(config.overrides[field] ?? "");
    try {
      const resp = await fetch("/api/generate/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product: selectedProduct, template: config, field }),
      });
      if (!resp.ok) throw new Error(await resp.text());
      setPreviewResult(await resp.json());
    } catch (e: any) {
      toast({ title: "Preview failed", description: e.message, variant: "destructive" });
    } finally {
      setPreviewLoading(false);
    }
  };

  // ── Run full generation ─────────────────────────────────────────────────
  const handleRun = async () => {
    if (!selectedProduct) {
      toast({ title: "No product selected", description: "Pick a product first.", variant: "destructive" });
      return;
    }
    setRunning(true);
    setJob(null);
    try {
      const resp = await fetch("/api/generate/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product: selectedProduct, template: config }),
      });
      if (!resp.ok) throw new Error(await resp.text());
      const result: GenerationJob = await resp.json();
      setJob(result);
      const ok = Object.values(result.fields).filter((f) => f.status === "ok").length;
      toast({ title: "Generation complete", description: `${ok} / ${result.totalFields} fields generated.` });
    } catch (e: any) {
      toast({ title: "Generation failed", description: e.message, variant: "destructive" });
    } finally {
      setRunning(false);
    }
  };

  const enabledCount = FIELD_LIST.filter((f) => config.fields[f]?.enabled).length;

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row justify-between gap-4 items-start sm:items-center">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
            <Sparkles className="w-7 h-7 text-primary" />
            Content Generation
          </h1>
          <p className="text-muted-foreground mt-1">
            Configure and generate product content fields from your Sunsky data.
          </p>
        </div>
        <button
          onClick={handleRun}
          disabled={running || !selectedProduct}
          className="px-5 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:translate-y-0"
        >
          {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
          Run Generation
        </button>
      </div>

      {/* Product Selector */}
      <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3 flex items-center gap-2">
          <Info className="w-3.5 h-3.5" /> Sample Product
        </h2>
        {loadingProducts ? (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading products…
          </div>
        ) : products.length === 0 ? (
          <p className="text-sm text-muted-foreground">No products found. Fetch some from Sunsky first.</p>
        ) : (
          <div className="flex gap-3 items-center">
            <select
              value={selectedProduct?.id ?? ""}
              onChange={(e) => {
                const p = products.find((x) => String(x.id) === e.target.value);
                setSelectedProduct(p ?? null);
              }}
              className={cn(inputCls, "max-w-sm")}
            >
              {products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.sku} — {p.name?.slice(0, 60)}
                </option>
              ))}
            </select>
            {selectedProduct && (
              <span className="text-xs text-muted-foreground px-3 py-1.5 rounded-lg bg-secondary border border-border">
                SKU: {selectedProduct.sku}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Main layout: Field Table + Preview */}
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-6">

        {/* Left: Global Settings + Field Table */}
        <div className="space-y-5">

          {/* Global Settings */}
          <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-4 flex items-center gap-2">
              <Zap className="w-3.5 h-3.5" /> Global Settings
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="flex items-center justify-between p-3 rounded-xl bg-secondary/30 border border-border/40">
                <div>
                  <p className="text-sm font-medium">AI Enabled</p>
                  <p className="text-xs text-muted-foreground">Use AI for 'ai' and 'hybrid' modes</p>
                </div>
                <Toggle
                  checked={config.globalSettings.ai_enabled}
                  onChange={(v) => patchGlobal({ ai_enabled: v })}
                />
              </div>

              <div className="p-3 rounded-xl bg-secondary/30 border border-border/40">
                <label className="text-xs text-muted-foreground">Max AI calls / product</label>
                <input
                  type="number"
                  value={config.globalSettings.max_calls_per_product}
                  min={1}
                  max={20}
                  onChange={(e) => patchGlobal({ max_calls_per_product: Number(e.target.value) })}
                  className="w-full mt-1 bg-background border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary"
                />
              </div>

              <div className="p-3 rounded-xl bg-secondary/30 border border-border/40">
                <label className="text-xs text-muted-foreground">Keyword Strategy</label>
                <select
                  value={config.globalSettings.keyword_strategy}
                  onChange={(e) => patchGlobal({ keyword_strategy: e.target.value })}
                  className="w-full mt-1 bg-background border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary"
                >
                  <option value="auto">Auto</option>
                  <option value="specs">From Specs</option>
                  <option value="name">From Name</option>
                  <option value="none">None</option>
                </select>
              </div>

              <div className="p-3 rounded-xl bg-secondary/30 border border-border/40">
                <label className="text-xs text-muted-foreground">Fallback Strategy</label>
                <select
                  value={config.globalSettings.fallback_strategy}
                  onChange={(e) => patchGlobal({ fallback_strategy: e.target.value })}
                  className="w-full mt-1 bg-background border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary"
                >
                  <option value="safe">Safe (use logic)</option>
                  <option value="skip">Skip field</option>
                  <option value="empty">Leave empty</option>
                </select>
              </div>
            </div>
          </div>

          {/* Field Table */}
          <div className="bg-card border border-border/50 rounded-2xl overflow-hidden shadow-sm">
            <div className="px-5 py-4 border-b border-border/50 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
                <Settings2 className="w-3.5 h-3.5" /> Fields
              </h2>
              <span className="text-xs text-muted-foreground">
                {enabledCount} / {FIELD_LIST.length} enabled
              </span>
            </div>
            <div className="divide-y divide-border/30">
              {FIELD_LIST.map((field) => {
                const fc = config.fields[field] ?? { enabled: true, mode: "logic" as Mode, options: {} };
                const hasOverride = !!config.overrides[field];

                return (
                  <div
                    key={field}
                    className={cn(
                      "flex items-center gap-4 px-5 py-3.5 transition-colors",
                      !fc.enabled && "opacity-50"
                    )}
                  >
                    {/* Enable toggle */}
                    <Toggle
                      checked={fc.enabled}
                      onChange={(v) => patchField(field, { enabled: v })}
                    />

                    {/* Field name */}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium flex items-center gap-2">
                        {FIELD_LABELS[field]}
                        {hasOverride && (
                          <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">
                            override
                          </span>
                        )}
                      </p>
                    </div>

                    {/* Mode selector */}
                    <select
                      value={fc.mode}
                      disabled={!fc.enabled}
                      onChange={(e) => patchField(field, { mode: e.target.value as Mode })}
                      className="bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-primary disabled:cursor-not-allowed"
                    >
                      {MODE_OPTIONS.map((m) => (
                        <option key={m} value={m} className="capitalize">
                          {m}
                        </option>
                      ))}
                    </select>

                    {/* Settings button */}
                    <button
                      disabled={!fc.enabled}
                      onClick={() => setPanelField(field)}
                      title="Configure field"
                      className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <Settings2 className="w-4 h-4" />
                    </button>

                    {/* Preview button */}
                    <button
                      disabled={!fc.enabled || !selectedProduct}
                      onClick={() => handlePreview(field)}
                      title="Preview"
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-secondary hover:bg-secondary/80 text-muted-foreground hover:text-foreground transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {previewLoading && previewingField === field ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <Eye className="w-3 h-3" />
                      )}
                      Preview
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right: Preview Panel + Results */}
        <div className="space-y-5">

          {/* Live Preview */}
          <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-sm min-h-[300px]">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-4 flex items-center gap-2">
              <Eye className="w-3.5 h-3.5" /> Preview
            </h2>
            <PreviewPanel
              field={previewingField}
              result={previewResult}
              override={previewOverride}
              onOverride={(v) => {
                setPreviewOverride(v);
                if (previewingField) setOverride(previewingField, v);
              }}
              onClearOverride={() => {
                setPreviewOverride("");
                if (previewingField) setOverride(previewingField, "");
              }}
            />
          </div>

          {/* Generation Results */}
          {job && (
            <div className="bg-card border border-border/50 rounded-2xl p-5 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> Results
                </h2>
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "text-xs px-2.5 py-1 rounded-full border font-medium",
                      job.status === "completed"
                        ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                        : "bg-amber-500/10 text-amber-400 border-amber-500/20"
                    )}
                  >
                    {job.status}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {job.doneFields}/{job.totalFields} fields
                  </span>
                </div>
              </div>

              {/* Progress bar */}
              <div className="w-full bg-secondary rounded-full h-1.5 mb-4">
                <div
                  className="bg-primary h-1.5 rounded-full transition-all duration-500"
                  style={{ width: `${(job.doneFields / Math.max(job.totalFields, 1)) * 100}%` }}
                />
              </div>

              <div className="space-y-2">
                {Object.values(job.fields).map((r) => (
                  <ResultRow key={r.field} result={r} />
                ))}
              </div>

              {/* Export hint */}
              <div className="mt-4 p-3 rounded-xl bg-secondary/30 border border-border/40 flex items-start gap-2">
                <Save className="w-4 h-4 text-muted-foreground mt-0.5 shrink-0" />
                <p className="text-xs text-muted-foreground">
                  Results are ready to apply to your WooCommerce products. Copy individual values
                  or use the override system to save custom edits.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Field Config Panel (slide-over) */}
      {panelField && (
        <FieldConfigPanel
          field={panelField}
          config={config.fields[panelField] ?? { enabled: true, mode: "logic", options: {} }}
          onChange={(fc) => patchField(panelField, fc)}
          onClose={() => setPanelField(null)}
        />
      )}
    </div>
  );
}
