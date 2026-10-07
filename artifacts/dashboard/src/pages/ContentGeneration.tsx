import { useStores } from "@/hooks/use-stores";
import { readSavedStore, saveStore } from "@/hooks/use-selected-store";
import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import {
  Sparkles, Settings2, Play, Eye, ChevronRight, CheckCircle2,
  XCircle, Loader2, RotateCcw, Save, Copy, X, Info, Zap, CheckCheck,
  ShieldCheck, RefreshCw
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const FIELD_LIST = [
  "title",
  "description",
  "short_description",
  "slug",
  "meta_title",
  "meta_description",
  "focus_keyword",
  "tags",
  "image_alt",
  "image_names",
  // Client feedback confirmed live via WordPress media library
  // screenshot: "all these fields should be here of wordpress
  // media." WordPress's own media attachment Caption and Description
  // fields, previously just a hardcoded reuse of Alt Text's value
  // with no field, toggle, or Settings visibility of their own.
  "image_caption",
  "image_description",
];

const FIELD_LABELS: Record<string, string> = {
  title: "Product Title",
  tags: "Tags",
  description: "Description",
  slug: "URL Slug",
  image_alt: "Image Alt Text",
  meta_title: "Meta Title",
  image_names: "Image File Names",
  short_description: "Short Description",
  meta_description: "Meta Description",
  focus_keyword: "Focus Keyword (Yoast/RankMath)",
  image_caption: "Image Caption (WordPress media)",
  image_description: "Image Description (WordPress media)",
};

const FIELD_DEPS: Record<string, string[]> = {
  slug: ["title"],
  image_alt: ["title"],
  meta_title: ["title"],
  image_names: ["slug"],
  short_description: ["description"],
  meta_description: ["description"],
  focus_keyword: ["title"],
  image_caption: ["image_alt"],
  image_description: ["short_description"],
};

const FIELD_DEFAULT_MODE: Record<string, string> = {
  title: "logic",
  tags: "logic",
  description: "ai",
  slug: "derive",
  image_alt: "derive",
  meta_title: "derive",
  image_names: "derive",
  short_description: "derive",
  meta_description: "derive",
  focus_keyword: "derive",
  image_caption: "derive",
  image_description: "derive",
};

const MODE_OPTIONS = ["logic", "ai", "derive"] as const;
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
  ai_provider: string;
  ai_model: string;
  ai_providers_enabled: Record<string, boolean>;
  max_calls_per_product: number;
  keyword_strategy: string;
  fallback_strategy: string;
  lock_specs_table: boolean;
  target_language: string;
}

interface ProviderInfo {
  configured: boolean;
  label: string;
  default_model: string;
  models: string[];
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

type OpenRouterModel = {
  id: string; name: string; context_length: number | null;
  input_per_million: number | null; output_per_million: number | null; is_free: boolean;
};

// "$0.15 / $0.60" per 1M tokens (input / output); "free" for free models.
export function formatOpenRouterPrice(m: OpenRouterModel): string {
  if (m.is_free) return "free";
  // at least 2 decimals, a 3rd only when needed: $0.60, $0.075, $30.00
  const f = (v: number | null) => {
    if (v == null) return "?";
    let t = v < 1 ? v.toFixed(3) : v.toFixed(2);
    if (v < 1 && t.endsWith("0")) t = t.slice(0, -1);
    return `$${t}`;
  };
  return `${f(m.input_per_million)} / ${f(m.output_per_million)}`;
}

// Filter + sort for the picker (pure, testable). "cheapest" orders by the
// cost of a typical product text (input + output price), free first.
export function filterOpenRouterModels(models: OpenRouterModel[], query: string, sort: "name" | "cheapest", freeOnly: boolean): OpenRouterModel[] {
  const q = query.trim().toLowerCase();
  let out = models.filter(m => (!freeOnly || m.is_free) && (!q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)));
  if (sort === "cheapest") {
    const cost = (m: OpenRouterModel) => m.is_free ? -1 : (m.input_per_million ?? Infinity) + (m.output_per_million ?? Infinity);
    out = [...out].sort((a, b) => cost(a) - cost(b) || a.name.localeCompare(b.name));
  }
  return out;
}

function OpenRouterModelPicker({ value, onChange }: { value: string; onChange: (m: string) => void }) {
  const [models, setModels] = useState<OpenRouterModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"name" | "cheapest">("cheapest");
  const [freeOnly, setFreeOnly] = useState(false);
  const load = (refresh = false) => {
    setError(null);
    fetch(`/api/generate/openrouter-models${refresh ? "?refresh=true" : ""}`)
      .then(async r => { if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail || `HTTP ${r.status}`); return r.json(); })
      .then(d => setModels(d.models ?? []))
      .catch(e => { setError(String(e.message || e)); setModels([]); });
  };
  useEffect(() => { load(); }, []);
  const shown = filterOpenRouterModels(models ?? [], query, sort, freeOnly).slice(0, 200);
  const selected = (models ?? []).find(m => m.id === value);
  return (
    <div className="p-3 rounded-xl bg-secondary/30 border border-border/40 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <label className="text-xs text-muted-foreground">Model (OpenRouter)</label>
        <button type="button" onClick={() => load(true)} className="text-[11px] text-muted-foreground hover:text-foreground">Refresh list</button>
      </div>
      <div className="text-sm">
        Selected: <span className="font-mono">{value || "openai/gpt-4o-mini (default)"}</span>
        {selected && <span className="ml-2 text-xs text-muted-foreground">{formatOpenRouterPrice(selected)} per 1M tokens (input / output)</span>}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search models, e.g. claude, gpt, gemini, llama…"
          className="flex-1 min-w-[12rem] bg-background border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary"
        />
        <select value={sort} onChange={e => setSort(e.target.value as any)}
                className="bg-background border border-border rounded-lg px-2 py-1.5 text-xs">
          <option value="cheapest">Cheapest first</option>
          <option value="name">By name</option>
        </select>
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          <input type="checkbox" checked={freeOnly} onChange={e => setFreeOnly(e.target.checked)} /> Free only
        </label>
      </div>
      {error && (
        <div className="text-xs text-red-400">
          Could not load the OpenRouter model list ({error}). You can still type a model id below.
        </div>
      )}
      {models === null ? (
        <div className="text-xs text-muted-foreground">Loading models…</div>
      ) : (
        <div className="max-h-64 overflow-auto rounded-lg border border-border/50 divide-y divide-border/30">
          {shown.map(m => (
            <button
              type="button"
              key={m.id}
              onClick={() => onChange(m.id)}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-3 hover:bg-secondary/60 ${m.id === value ? "bg-primary/10" : ""}`}
            >
              <span className="flex-1 min-w-0">
                <span className="text-foreground">{m.name}</span>
                <span className="block font-mono text-[10px] text-muted-foreground truncate">{m.id}</span>
              </span>
              <span className={`shrink-0 ${m.is_free ? "text-emerald-400" : "text-muted-foreground"}`}>{formatOpenRouterPrice(m)}</span>
            </button>
          ))}
          {shown.length === 0 && <div className="px-3 py-3 text-xs text-muted-foreground">No models match.</div>}
        </div>
      )}
      <div className="flex items-center gap-2">
        <input
          value={value}
          onChange={e => onChange(e.target.value.trim())}
          placeholder="…or type a model id, e.g. anthropic/claude-sonnet-4"
          className="flex-1 bg-background border border-border rounded-lg px-3 py-1.5 text-xs font-mono focus:outline-none focus:border-primary"
        />
      </div>
      <p className="text-[11px] text-muted-foreground">
        Prices are USD per 1M tokens (input / output), from OpenRouter. Batch processing stays Anthropic-only; OpenRouter runs live requests.
      </p>
    </div>
  );
}

const AI_PROVIDERS: Record<string, { label: string; models: string[]; defaultModel: string }> = {
  // Client request (point 7): OpenRouter + a list of the models it offers,
  // to test models and optimise cost. Its catalogue (hundreds of models,
  // changing often) is loaded live with prices -- see OpenRouterModelPicker.
  openrouter: {
    label: "OpenRouter",
    models: [],
    defaultModel: "openai/gpt-4o-mini",
  },
  openai: {
    label: "OpenAI",
    models: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
    defaultModel: "gpt-4o-mini",
  },
  anthropic: {
    label: "Anthropic (Claude)",
    // Client feedback: confirmed live via preview panel that Title/
    // Description were falling back to logic mode ("logic:fallback"),
    // and the Model dropdown was defaulted to claude-3-haiku-20240307
    // -- a 2024-era model, badly outdated given the current lineup.
    // Verified current model strings via web search, cross-referenced
    // against Anthropic's own model overview docs (fetched live).
    models: ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
    defaultModel: "claude-sonnet-5",
  },
  gemini: {
    label: "Google Gemini",
    // Client feedback: "Please update the models here, some are out
    // of date." Verified directly against Google's own official docs
    // (ai.google.dev/gemini-api/docs/models, last updated 2026-08-26):
    // gemini-2.0-flash and gemini-2.0-flash-lite are BOTH explicitly
    // marked "(Shut down)" there -- and gemini-2.0-flash-lite was the
    // client's currently-active model, meaning every Gemini call was
    // silently failing (404) the whole time. The entire gemini-1.5
    // series is also confirmed completely shut down (all requests
    // return 404, per Google's Firebase AI Logic docs). Replaced with
    // the current stable + preview lineup.
    //
    // CORRECTION to the original patch 91 default: client sent their
    // own working system (a separate HTML tool) which defaults to
    // gemini-2.5-flash specifically, explaining their "I need to test
    // the free tier of gemini flash 2.5 as in my system works"
    // feedback. Verified via multiple current sources: the entire
    // Gemini 3.x series requires paid billing and is NOT available on
    // the free tier at all, while gemini-2.5-flash remains free and
    // is not deprecated (unlike 1.5/2.0, which genuinely are). Kept
    // the 3.x models available in the list for anyone who wants to
    // pay for them, but reverted the DEFAULT to the free-tier model.
    models: [
      "gemini-2.5-flash",
      "gemini-2.5-pro",
      "gemini-2.5-flash-lite",
      "gemini-3.7-flash",
      "gemini-3.6-flash",
      "gemini-3.5-flash",
      "gemini-3.5-flash-lite",
      "gemini-3.1-pro-preview",
      "gemini-3.1-flash-lite",
    ],
    defaultModel: "gemini-3.5-flash-lite",   // 2.5 models aren't offered to new Google accounts (PL-162: 404)
  },
};

const DEFAULT_CONFIG: GenerateConfig = {
  globalSettings: {
    ai_enabled: false,
    ai_provider: "openai",
    ai_model: "",
    ai_providers_enabled: { openai: true, anthropic: true, gemini: true, openrouter: true },
    max_calls_per_product: 3,
    keyword_strategy: "auto",
    fallback_strategy: "safe",
    lock_specs_table: false,
    target_language: "bg",
  },
  fields: Object.fromEntries(
    FIELD_LIST.map((f) => [
      f,
      {
        enabled: true,
        mode: (FIELD_DEFAULT_MODE[f] ?? "logic") as Mode,
        options:
          f === "title"
            ? { max_chars: 120 }
            : f === "description"
            ? { structure: ["intro", "features", "benefits", "compatibility", "closing"], keyword_source: "auto", max_chars: 2000 }
            : f === "slug"
            ? { max_chars: 70, ensure_unique: true }
            : f === "meta_title"
            ? { max_chars: 60 }
            : f === "meta_description"
            ? { max_chars: 160 }
            : f === "tags"
            ? { max_tags: 3, include_specs: true }
            : f === "image_alt"
            ? { max_chars: 125, include_sku: true }
            : f === "image_names"
            ? { max_chars: 70 }
            : f === "short_description"
            ? { max_chars: 400 }
            : f === "focus_keyword"
            ? { max_chars: 60 }
            : {},
      },
    ])
  ),
  overrides: {},
};

// ─────────────────────────────────────────────────────────────────────────────
// Mode descriptions shown in configure panel
// ─────────────────────────────────────────────────────────────────────────────

const MODE_DESCRIPTIONS: Record<string, { title: string; desc: string; color: string }> = {
  logic: {
    title: "Logic (rule-based)",
    desc: "Generates content from product data: SKU, category, specs table, brand. Fast, deterministic — no API key needed. Always available as fallback.",
    color: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  },
  ai: {
    title: "AI (model-generated)",
    desc: "Uses an AI model (OpenAI / Claude / Gemini) to write the field. Produces the most natural copy. Requires a configured API key. Falls back to logic if the call fails.",
    color: "text-violet-400 bg-violet-500/10 border-violet-500/20",
  },
  derive: {
    title: "Derive (auto-computed)",
    desc: "The value is calculated automatically from another field — no AI, no extra config. E.g. slug derives from title, meta title derives from title + brand. Fastest option for these fields.",
    color: "text-sky-400 bg-sky-500/10 border-sky-500/20",
  },
};

// Validation rules per field (mirrors backend VALIDATORS)
const FIELD_RULES: Record<string, string[]> = {
  title: ["Max 120 characters", "CSV title used first if available"],
  slug: ["Max 70 characters", "Lowercase, hyphens only — no spaces", "Append SKU for uniqueness (toggle below)"],
  tags: ["Maximum 3 tags", "Extracted from name + specs table"],
  image_alt: ["Max 125 characters", 'Format: "Title – Attribute – Brand"'],
  image_names: ["Max 70 chars per name", 'Format: "{slug}-1.webp"'],
  image_caption: ["Max 125 characters", "Same value as Image Alt Text by default", "WordPress media library's Caption field"],
  image_description: ["Max 300 characters", "Reuses Short Description by default", "WordPress media library's Description field"],
  description: ["50 – 300 words", 'Banned phrases: "the best", "100%", "guarantee"', "Structured sections configurable below"],
  short_description: ["Max 400 characters", "Plain text (no HTML)"],
  meta_title: ["Max 60 characters", 'Format: "Title | Brand"'],
  meta_description: ["80 – 160 characters", "Ends with a call-to-action phrase"],
  focus_keyword: ["Max 60 characters", "2–5 word search phrase", "Written to _yoast_wpseo_focuskw and rank_math_focus_keyword"],
};

// ─────────────────────────────────────────────────────────────────────────────
// Config migration (old → new format, hybrid → derive)
// ─────────────────────────────────────────────────────────────────────────────

function migrateConfig(raw: any): GenerateConfig {
  const base = DEFAULT_CONFIG;
  const fields: Record<string, FieldConfig> = {};
  for (const f of FIELD_LIST) {
    const saved = raw?.fields?.[f];
    if (!saved) {
      fields[f] = base.fields[f];
    } else {
      const rawMode = saved.mode === "hybrid" ? "derive" : (saved.mode ?? base.fields[f].mode);
      fields[f] = {
        enabled: saved.enabled ?? true,
        mode: rawMode as Mode,
        options: saved.options ?? base.fields[f].options ?? {},
      };
    }
  }
  return {
    globalSettings: { ...base.globalSettings, ...(raw?.globalSettings ?? {}) },
    fields,
    overrides: raw?.overrides ?? {},
  };
}

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
  // Guard: options may be missing from old saved configs
  const opt: FieldOptions = config.options ?? {};

  const setOpt = (patch: Partial<FieldOptions>) =>
    onChange({ ...config, options: { ...opt, ...patch } });

  const toggleStructure = (item: string) => {
    const cur = opt.structure ?? [];
    setOpt({ structure: cur.includes(item) ? cur.filter((x) => x !== item) : [...cur, item] });
  };

  const modeInfo = MODE_DESCRIPTIONS[config.mode];
  const rules = FIELD_RULES[field] ?? [];
  const deps = FIELD_DEPS[field] ?? [];

  const panel = (
    <div className="fixed inset-0 z-[9999] flex justify-end" style={{ position: "fixed" }}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        className="relative w-full max-w-md bg-card border-l border-border shadow-2xl flex flex-col"
        style={{ height: "100vh", overflowY: "auto" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-border/50 sticky top-0 bg-card z-10">
          <div>
            <h3 className="font-semibold text-foreground text-base">{label}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">Field configuration</p>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-secondary text-muted-foreground transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-6">

          {/* ── Mode selector ───────────────────────────────────────────── */}
          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Generation Mode</label>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {MODE_OPTIONS.map((m) => (
                <button
                  key={m}
                  type="button"
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

          {/* ── Mode explanation ─────────────────────────────────────────── */}
          {modeInfo && (
            <div className={cn("p-3 rounded-xl border", modeInfo.color)}>
              <p className={cn("text-xs font-semibold mb-1", modeInfo.color.split(" ")[0])}>
                {modeInfo.title}
              </p>
              <p className="text-xs text-muted-foreground leading-relaxed">{modeInfo.desc}</p>
              {config.mode === "derive" && deps.length > 0 && (
                <p className="text-xs mt-2">
                  <span className="text-muted-foreground">Computed from: </span>
                  {deps.map((d) => (
                    <span key={d} className="font-mono text-foreground bg-secondary px-1.5 py-0.5 rounded mr-1 text-[10px]">{d}</span>
                  ))}
                </p>
              )}
            </div>
          )}

          {/* ── Validation rules ─────────────────────────────────────────── */}
          {rules.length > 0 && (
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                <ShieldCheck className="w-3 h-3" /> Validation Rules
              </label>
              <ul className="mt-2 space-y-1.5">
                {rules.map((r) => (
                  <li key={r} className="flex items-start gap-2 text-xs text-muted-foreground">
                    <span className="mt-0.5 w-1.5 h-1.5 rounded-full bg-border shrink-0" />
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* ── Field-specific options ────────────────────────────────────── */}
          {field === "description" && (
            <>
              <div>
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Max Characters</label>
                <input
                  type="number"
                  value={opt.max_chars ?? 2000}
                  min={200} max={5000}
                  onChange={(e) => setOpt({ max_chars: Number(e.target.value) })}
                  className={cn(inputCls, "mt-2")}
                />
                <p className="text-xs text-muted-foreground mt-1">Counts visible text only, not HTML tags. Trims by whole paragraph/list item, never mid-word.</p>
              </div>
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

          {field === "slug" && (
            <>
              <div className="flex items-center justify-between p-3 rounded-xl bg-secondary/40 border border-border/40">
                <div>
                  <p className="text-sm font-medium">Transliterate</p>
                  <p className="text-xs text-muted-foreground">Convert non-ASCII chars (e.g. é → e)</p>
                </div>
                <Toggle checked={!!opt.transliterate} onChange={(v) => setOpt({ transliterate: v })} />
              </div>
              <div className="flex items-center justify-between p-3 rounded-xl bg-secondary/40 border border-border/40">
                <div>
                  <p className="text-sm font-medium">Append SKU suffix</p>
                  <p className="text-xs text-muted-foreground">Ensures uniqueness across products</p>
                </div>
                <Toggle checked={!!opt.ensure_unique} onChange={(v) => setOpt({ ensure_unique: v })} />
              </div>
            </>
          )}

          {field === "image_alt" && (
            <div className="flex items-center justify-between p-3 rounded-xl bg-secondary/40 border border-border/40">
              <div>
                <p className="text-sm font-medium">Include SKU in alt text</p>
                <p className="text-xs text-muted-foreground">Appends the product SKU to the alt tag</p>
              </div>
              <Toggle checked={!!opt.include_sku} onChange={(v) => setOpt({ include_sku: v })} />
            </div>
          )}

          {field === "tags" && (
            <div className="flex items-center justify-between p-3 rounded-xl bg-secondary/40 border border-border/40">
              <div>
                <p className="text-sm font-medium">Include spec values</p>
                <p className="text-xs text-muted-foreground">Extract tags from the product spec table</p>
              </div>
              <Toggle checked={!!opt.include_specs} onChange={(v) => setOpt({ include_specs: v })} />
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

          {/* ── Max Characters (per field) ────────────────────────────── */}
          {field === "title" && (
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Max Characters</label>
              <input
                type="number"
                value={opt.max_chars ?? 120}
                min={20} max={300}
                onChange={(e) => setOpt({ max_chars: Number(e.target.value) })}
                className={cn(inputCls, "mt-2")}
              />
            </div>
          )}

          {(field === "meta_title" || field === "meta_description" || field === "slug" ||
            field === "image_alt" || field === "short_description" || field === "image_names" ||
            field === "focus_keyword") && (
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Max Characters</label>
              <input
                type="number"
                value={opt.max_chars ?? (
                  field === "meta_title" ? 60 :
                  field === "meta_description" ? 160 :
                  field === "slug" ? 70 :
                  field === "image_alt" ? 125 :
                  field === "short_description" ? 400 :
                  field === "focus_keyword" ? 60 : 70
                )}
                min={20} max={500}
                onChange={(e) => setOpt({ max_chars: Number(e.target.value) })}
                className={cn(inputCls, "mt-2")}
              />
            </div>
          )}

          {field === "tags" && (
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Max Tags</label>
              <input
                type="number"
                value={opt.max_tags ?? 3}
                min={1} max={10}
                onChange={(e) => setOpt({ max_tags: Number(e.target.value) })}
                className={cn(inputCls, "mt-2")}
              />
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-border/50 sticky bottom-0 bg-card">
          <button
            type="button"
            onClick={onClose}
            className="w-full py-2.5 rounded-xl bg-primary text-primary-foreground font-medium text-sm hover:bg-primary/90 transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(panel, document.body);
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
          {/* Client: tested several models and got "logic:fallback" with no way
              to see why -- the reason (provider error, rate limit, empty
              reply ...) was in the result but only shown for "failed". */}
          {result.status !== "failed" && String(result.source ?? "").includes("fallback") && result.error && (
            <div className="mb-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-300 text-xs break-words">
              <span className="font-semibold">AI failed — template text used instead.</span> Reason: {String(result.error).slice(0, 600)}
            </div>
          )}
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
          {/* Client: tested several models and got "logic:fallback" with no way
              to see why -- the reason (provider error, rate limit, empty
              reply ...) was in the result but only shown for "failed". */}
          {result.status !== "failed" && String(result.source ?? "").includes("fallback") && result.error && (
            <div className="mb-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-300 text-xs break-words">
              <span className="font-semibold">AI failed — template text used instead.</span> Reason: {String(result.error).slice(0, 600)}
            </div>
          )}
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
  const [savedConfig, setSavedConfig] = useState<GenerateConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const justSavedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // AI provider status
  const [providerStatus, setProviderStatus] = useState<Record<string, ProviderInfo>>({});

  const hasUnsavedChanges = savedConfig !== null &&
    JSON.stringify(config) !== JSON.stringify(savedConfig);

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

  // ── Store: global defaults or one store's own settings ──────────────────
  // Client: "how can I control the content generation option for different
  // store? Right now they are all global". null = global defaults; a store
  // uses its custom settings if it has them, else the global ones.
  const { data: cgStores } = useStores();
  const [cgStore, setCgStore] = useState<number | null>(() => { const v = readSavedStore(); return typeof v === "number" ? v : null; });
  const [customStoreIds, setCustomStoreIds] = useState<number[]>([]);
  const reloadCustomIds = () =>
    fetch("/api/generate/store-configs").then(r => r.ok ? r.json() : { store_ids: [] })
      .then(d => setCustomStoreIds(d.store_ids ?? [])).catch(() => {});
  useEffect(() => { reloadCustomIds(); }, []);
  const cgStoreName = ((cgStores ?? []) as any[]).find(st => st.id === cgStore)?.name;
  const cgStoreCustom = cgStore !== null && customStoreIds.includes(cgStore);
  const cfgQuery = cgStore !== null ? `?store_id=${cgStore}` : "";

  // ── Load saved config + provider status on mount / store change ─────────
  useEffect(() => {
    fetch(`/api/generate/saved-config${cfgQuery}`)
      .then((r) => r.json())
      .then((data) => {
        const migrated = migrateConfig(data);
        setConfig(migrated);
        setSavedConfig(migrated);
      })
      .catch(() => { setSavedConfig(DEFAULT_CONFIG); });
  }, [cfgQuery]);
  useEffect(() => {
    fetch("/api/generate/providers")
      .then((r) => r.json())
      .then((data) => setProviderStatus(data))
      .catch(() => {});
  }, []);

  const handleRevertToGlobal = async () => {
    if (cgStore === null) return;
    const r = await fetch(`/api/generate/saved-config?store_id=${cgStore}`, { method: "DELETE" });
    if (!r.ok) { toast({ title: "Revert failed", variant: "destructive" }); return; }
    await reloadCustomIds();
    const d = await fetch(`/api/generate/saved-config?store_id=${cgStore}`).then(x => x.json());
    const migrated = migrateConfig(d); setConfig(migrated); setSavedConfig(migrated);
    toast({ title: "Using global settings", description: `${cgStoreName ?? "This store"} now follows the global Content Generation settings.` });
  };

  // ── Save config to server ────────────────────────────────────────────────
  const handleSaveConfig = async () => {
    setSaving(true);
    try {
      const r = await fetch(`/api/generate/saved-config${cfgQuery}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (!r.ok) throw new Error(await r.text());
      setSavedConfig({ ...config });
      setJustSaved(true);
      if (justSavedTimer.current) clearTimeout(justSavedTimer.current);
      justSavedTimer.current = setTimeout(() => setJustSaved(false), 2500);
      if (cgStore !== null) reloadCustomIds();
      toast({ title: "Config saved", description: cgStore !== null
        ? `Custom settings saved for ${cgStoreName ?? "this store"} — its pipelines use them.`
        : "Global settings saved — used by every store without custom settings." });
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

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
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <button
            type="button"
            onClick={() => {
              setConfig(DEFAULT_CONFIG);
              toast({ title: "Reset to defaults", description: "Click Save to apply. Derive mode set for computed fields." });
            }}
            className="px-4 py-2.5 rounded-xl font-semibold text-sm transition-all flex items-center gap-2 border shadow-sm bg-secondary text-muted-foreground border-border hover:text-foreground hover:bg-secondary/80"
            title="Reset all field modes to smart defaults (derive for computed fields)"
          >
            <RefreshCw className="w-4 h-4" />
            Reset Defaults
          </button>
          <button
            onClick={handleSaveConfig}
            disabled={saving}
            className="px-4 py-2.5 rounded-xl font-semibold text-sm transition-all flex items-center gap-2 border shadow-sm bg-amber-400 text-black border-amber-300 hover:bg-amber-300"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {/* Client: "I am not sure how this setting - use global settings
                works". The button now says exactly WHERE this save goes. */}
            {cgStore === null
              ? "Save global settings"
              : cgStoreCustom
                ? `Save for ${cgStoreName ?? "this store"}`
                : `Save as custom for ${cgStoreName ?? "this store"}`}
          </button>
          <button
            onClick={handleRun}
            disabled={running || !selectedProduct}
            className="px-5 py-2.5 rounded-xl bg-primary text-primary-foreground font-medium transition-all shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:-translate-y-0.5 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:translate-y-0"
          >
            {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
            Test on Sample Product
          </button>
        </div>
      </div>
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 px-4 py-3 rounded-xl border border-border/50 bg-card">
        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider shrink-0">Settings for</label>
        <select
          value={cgStore === null ? "" : String(cgStore)}
          onChange={e => { const v = e.target.value === "" ? null : Number(e.target.value); setCgStore(v); saveStore(v === null ? "global" : v); }}
          className="bg-background border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary sm:w-64"
        >
          <option value="">Global default (all stores)</option>
          {((cgStores ?? []) as any[]).map(st => (
            <option key={st.id} value={st.id}>{st.name}{customStoreIds.includes(st.id) ? " — custom" : ""}</option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground flex-1">
          {cgStore === null
            ? "The global settings: used by every store that has no custom settings of its own."
            : cgStoreCustom
              ? `${cgStoreName ?? "This store"} has its own settings, separate from the global ones (including which AI providers are on). Its pipelines use these; changing the global settings does not affect it.`
              : `${cgStoreName ?? "This store"} follows the global settings, shown below. Saving here creates separate settings for ${cgStoreName ?? "this store"} only — to change all stores, choose "Global default" above.`}
        </p>
        {/* Something always sits here, so nothing "disappears" (client: "Had
            button here and I click on it, and it disappear"): a button while
            the store has its own settings, a status label once it follows
            the global ones. */}
        {cgStoreCustom && (
          <button type="button" onClick={handleRevertToGlobal}
                  title="Deletes this store's own settings. The store then follows the global settings again."
                  className="px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-secondary hover:bg-secondary/80 text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap">
            Remove custom settings — use global
          </button>
        )}
        {cgStore !== null && !cgStoreCustom && (
          <span className="px-3 py-1.5 rounded-lg text-xs font-medium border border-emerald-500/25 bg-emerald-500/10 text-emerald-400 whitespace-nowrap"
                title="This store has no settings of its own. It uses the global settings.">
            ✓ Following global settings
          </span>
        )}
      </div>
      {hasUnsavedChanges && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-amber-500/8 border border-amber-500/15 text-xs text-amber-400/80">
          <Info className="w-3.5 h-3.5 shrink-0" />
          You have unsaved changes — save to use these settings in pipelines
        </div>
      )}

      {/* ── AI enabled but no API key configured ─── */}
      {(() => {
        const gs = config.globalSettings;
        const provider = gs.ai_provider;
        const aiFieldCount = FIELD_LIST.filter((f) => config.fields[f]?.mode === "ai").length;
        const keyMissing = gs.ai_enabled && aiFieldCount > 0 && provider &&
          providerStatus[provider] && !providerStatus[provider].configured;
        if (!keyMissing) return null;
        const envVar = provider === "openai" ? "OPENAI_API_KEY"
          : provider === "anthropic" ? "ANTHROPIC_API_KEY"
          : "GEMINI_API_KEY";
        return (
          <div className="flex items-start gap-3 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/25 text-sm text-red-300">
            <XCircle className="w-4 h-4 shrink-0 mt-0.5 text-red-400" />
            <div>
              <p className="font-semibold text-red-400">
                AI is enabled but <code className="font-mono px-1 bg-red-500/15 rounded">{envVar}</code> is not set
              </p>
              <p className="text-xs text-red-300/80 mt-1">
                Every field set to <strong>ai</strong> mode will fall back to logic — this is why you see
                "logic fallback (AI failed)" in your pipeline logs.
                Add the key in <strong>Settings → Environment variables</strong>, then restart the Python API.
                Alternatively, switch those {aiFieldCount} field{aiFieldCount !== 1 ? "s" : ""} to{" "}
                <strong>logic</strong> or <strong>derive</strong> mode, or click{" "}
                <strong>Reset Defaults</strong> above.
              </p>
            </div>
          </div>
        );
      })()}

      <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-amber-500/8 border border-amber-500/15 text-xs text-amber-400/80 italic">
        <Info className="w-3.5 h-3.5 shrink-0" />
        Preview results shown here only — save config to apply settings to new pipeline runs
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
                  <p className="text-xs text-muted-foreground">Use AI for fields set to 'ai' mode</p>
                </div>
                <Toggle
                  checked={config.globalSettings.ai_enabled}
                  onChange={(v) => patchGlobal({ ai_enabled: v })}
                />
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-secondary/30 border border-border/40">
                <div>
                  <p className="text-sm font-medium">Lock Specs Table</p>
                  <p className="text-xs text-muted-foreground">Block the raw Sunsky specs table from every mode (AI, Logic, Derive)</p>
                </div>
                <Toggle
                  checked={config.globalSettings.lock_specs_table}
                  onChange={(v) => patchGlobal({ lock_specs_table: v })}
                />
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-secondary/30 border border-border/40">
                <div>
                  <p className="text-sm font-medium">Target Language</p>
                  <p className="text-xs text-muted-foreground">Language for generated content (title, description, SEO fields, etc.) — brand/model names are never translated. Slug and image filenames always stay in English.</p>
                </div>
                <select
                  value={config.globalSettings.target_language ?? "bg"}
                  onChange={(e) => patchGlobal({ target_language: e.target.value })}
                  className="bg-background border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary shrink-0"
                >
                  <option value="bg">Bulgarian</option>
                  <option value="en">English</option>
                </select>
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

            {/* AI Provider + Model — shown when AI is enabled */}
            {config.globalSettings.ai_enabled && (
              <div className="mt-4 pt-4 border-t border-border/40 space-y-3">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Sparkles className="w-3 h-3 text-violet-400" /> AI Provider Settings
                </p>

                {/* Provider cards with enable/disable toggles */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {Object.entries(AI_PROVIDERS).map(([id, info]) => {
                    const status = providerStatus[id];
                    const isEnabled = config.globalSettings.ai_providers_enabled?.[id] ?? true;
                    const isSelected = config.globalSettings.ai_provider === id && isEnabled;
                    const isConfigured = status?.configured ?? false;

                    const handleToggleEnabled = (e: React.MouseEvent) => {
                      e.stopPropagation();
                      const newEnabled = { ...(config.globalSettings.ai_providers_enabled ?? {}), [id]: !isEnabled };
                      const patch: any = { ai_providers_enabled: newEnabled };
                      // Client feedback confirmed live: toggling a
                      // provider's "enabled" switch on did NOT make it
                      // the active provider -- a separate "Use this"
                      // click was required, which is easy to miss.
                      // Result: Gemini was toggled on, but generation
                      // silently kept using OpenAI (still the active
                      // provider), which had exhausted its quota --
                      // every AI call failed and fell back to Logic
                      // mode with no clear indication of why. Turning a
                      // provider on now also selects it as active,
                      // matching what a user naturally expects.
                      if (!isEnabled) {
                        patch.ai_provider = id;
                        patch.ai_model = "";
                      } else if (config.globalSettings.ai_provider === id) {
                        // Client: "enable two or more, then disable them -- the
                        // model dropdown still shows the disabled provider's
                        // models". Turning OFF the active provider now hands
                        // "active" to another enabled one (with a key first).
                        const others = Object.keys(AI_PROVIDERS).filter(p => p !== id && (newEnabled[p] ?? true));
                        const next = others.find(p => providerStatus[p]?.configured) ?? others[0];
                        if (next) { patch.ai_provider = next; patch.ai_model = ""; }
                      }
                      patchGlobal(patch);
                    };

                    return (
                      <div
                        key={id}
                        className={cn(
                          "rounded-xl border transition-all",
                          isSelected
                            ? "border-primary bg-primary/10"
                            : isEnabled
                              ? "border-border/40 bg-secondary/30"
                              : "border-border/20 bg-secondary/10 opacity-50"
                        )}
                      >
                        {/* Top row: label + enable toggle */}
                        <div className="flex items-center justify-between px-3 pt-3 pb-1">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className={cn(
                              "w-2 h-2 rounded-full shrink-0",
                              isConfigured ? "bg-emerald-400" : "bg-red-400"
                            )} />
                            <p className="text-xs font-medium truncate">{info.label}</p>
                          </div>
                          <button
                            type="button"
                            onClick={handleToggleEnabled}
                            title={isEnabled ? "Disable provider" : "Enable provider"}
                            className={cn(
                              "relative inline-flex h-4 w-7 cursor-pointer rounded-full border-2 border-transparent transition-colors shrink-0",
                              isEnabled ? "bg-primary" : "bg-secondary"
                            )}
                          >
                            <span className={cn(
                              "pointer-events-none inline-block h-3 w-3 transform rounded-full bg-white shadow transition duration-200",
                              isEnabled ? "translate-x-3" : "translate-x-0"
                            )} />
                          </button>
                        </div>
                        {/* Bottom row: status + select button */}
                        <div className="flex items-center justify-between px-3 pb-3 pt-1">
                          <p className="text-[10px] text-muted-foreground">
                            {isConfigured ? "API key set" : "No API key"}
                          </p>
                          {isEnabled && (
                            <button
                              type="button"
                              disabled={!isEnabled}
                              onClick={() => patchGlobal({ ai_provider: id, ai_model: "" })}
                              className={cn(
                                "text-[10px] px-2 py-0.5 rounded-md font-medium transition-all",
                                isSelected
                                  ? "bg-primary text-primary-foreground"
                                  : "bg-secondary text-muted-foreground hover:text-foreground hover:bg-secondary/80"
                              )}
                            >
                              {isSelected ? "Active" : "Use this"}
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Warning if selected provider has no API key */}
                {config.globalSettings.ai_provider &&
                  providerStatus[config.globalSettings.ai_provider] &&
                  !providerStatus[config.globalSettings.ai_provider].configured && (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
                    <Info className="w-3.5 h-3.5 shrink-0" />
                    Set <strong className="mx-1">
                      {config.globalSettings.ai_provider.toUpperCase()}_API_KEY
                    </strong> in your <code className="mx-1 px-1 bg-red-500/10 rounded">.env</code> file, then restart the server.
                  </div>
                )}

                {/* Model selector -- only for an ENABLED active provider */}
                {!(config.globalSettings.ai_providers_enabled?.[config.globalSettings.ai_provider] ?? true) ? (
                  <div className="p-3 rounded-xl bg-secondary/30 border border-border/40 text-xs text-muted-foreground">
                    No AI provider is enabled — turn one on above to choose a model.
                  </div>
                ) : config.globalSettings.ai_provider === "openrouter" ? (
                  <OpenRouterModelPicker
                    value={config.globalSettings.ai_model || ""}
                    onChange={(m) => patchGlobal({ ai_model: m })}
                  />
                ) : (
                <div className="p-3 rounded-xl bg-secondary/30 border border-border/40">
                  <label className="text-xs text-muted-foreground">Model</label>
                  <select
                    value={config.globalSettings.ai_model || ""}
                    onChange={(e) => patchGlobal({ ai_model: e.target.value })}
                    className="w-full mt-1 bg-background border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-primary"
                  >
                    <option value="">
                      Default ({AI_PROVIDERS[config.globalSettings.ai_provider]?.defaultModel ?? "auto"})
                    </option>
                    {(AI_PROVIDERS[config.globalSettings.ai_provider]?.models ?? []).map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                </div>
                )}
              </div>
            )}
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
