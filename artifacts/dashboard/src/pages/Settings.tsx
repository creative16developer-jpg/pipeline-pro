import { useState, useEffect } from "react";
import {
  Settings as SettingsIcon, Key, Eye, EyeOff, CheckCircle2,
  XCircle, Save, Trash2, Loader2, Info, Sparkles, ExternalLink,
  RefreshCw
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

interface ProviderStatus {
  configured: boolean;
  source: "env" | "file" | "none";
  masked: string | null;
}

const PROVIDERS: Record<string, {
  label: string;
  description: string;
  envVar: string;
  docsUrl: string;
  docsLabel: string;
  color: string;
  bgColor: string;
  models: string[];
}> = {
  gemini: {
    label: "Google Gemini",
    description: "gemini-2.0-flash, gemini-1.5-pro and other Google models",
    envVar: "GEMINI_API_KEY",
    docsUrl: "https://aistudio.google.com/apikey",
    docsLabel: "Google AI Studio",
    color: "text-sky-400",
    bgColor: "bg-sky-500/10 border-sky-500/20",
    models: [
      "gemini-2.5-flash",
      "gemini-2.5-pro",
      "gemini-2.0-flash",
      "gemini-2.0-flash-lite",
      "gemini-1.5-pro",
      "gemini-1.5-flash",
      "gemini-1.5-flash-8b",
    ],
  },
  openai: {
    label: "OpenAI",
    description: "gpt-4o-mini, gpt-4o, gpt-4-turbo and other OpenAI models",
    envVar: "OPENAI_API_KEY",
    docsUrl: "https://platform.openai.com/api-keys",
    docsLabel: "OpenAI Platform",
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10 border-emerald-500/20",
    models: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
  },
  anthropic: {
    label: "Anthropic (Claude)",
    description: "claude-3-haiku, claude-3.5-sonnet and other Claude models",
    envVar: "ANTHROPIC_API_KEY",
    docsUrl: "https://console.anthropic.com/",
    docsLabel: "Anthropic Console",
    color: "text-amber-400",
    bgColor: "bg-amber-500/10 border-amber-500/20",
    models: ["claude-3-haiku-20240307", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
  },
};

const inputCls =
  "w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary font-mono placeholder:font-sans placeholder:text-muted-foreground";

export default function Settings() {
  const { toast } = useToast();

  const [statuses, setStatuses] = useState<Record<string, ProviderStatus>>({});
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadStatuses = () => {
    setLoading(true);
    fetch("/api/settings/api-keys")
      .then((r) => r.json())
      .then(setStatuses)
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadStatuses(); }, []);

  const handleSave = async (provider: string) => {
    const key = (keyInputs[provider] ?? "").trim();
    if (!key) return;
    setSaving(provider);
    try {
      const r = await fetch("/api/settings/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [provider]: key }),
      });
      if (!r.ok) throw new Error(await r.text());
      // Refresh statuses from server
      const updated = await fetch("/api/settings/api-keys").then((x) => x.json());
      setStatuses(updated);
      setKeyInputs((prev) => ({ ...prev, [provider]: "" }));
      toast({
        title: "API key saved",
        description: `${PROVIDERS[provider]?.label} key is now active for pipelines.`,
      });
    } catch (e: any) {
      toast({ title: "Save failed", description: e.message, variant: "destructive" });
    } finally {
      setSaving(null);
    }
  };

  const handleRemove = async (provider: string) => {
    setRemoving(provider);
    try {
      const r = await fetch(`/api/settings/api-keys/${provider}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      const updated = await fetch("/api/settings/api-keys").then((x) => x.json());
      setStatuses(updated);
      toast({ title: "Key removed", description: "The stored API key has been deleted." });
    } catch (e: any) {
      toast({ title: "Remove failed", description: e.message, variant: "destructive" });
    } finally {
      setRemoving(null);
    }
  };

  const configuredCount = Object.values(statuses).filter((s) => s.configured).length;

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-display font-bold text-foreground flex items-center gap-3">
            <SettingsIcon className="w-7 h-7 text-primary" />
            Settings
          </h1>
          <p className="text-muted-foreground mt-1">
            Configure API keys and pipeline preferences.
          </p>
        </div>
        <button
          onClick={loadStatuses}
          disabled={loading}
          className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          title="Refresh status"
        >
          <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
        </button>
      </div>

      {/* AI Provider Keys */}
      <div className="bg-card border border-border/50 rounded-2xl shadow-sm overflow-hidden">
        {/* Section header */}
        <div className="px-6 py-4 border-b border-border/50 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-violet-400" />
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
              AI Provider Keys
            </h2>
          </div>
          <span className="text-xs text-muted-foreground">
            {configuredCount} / {Object.keys(PROVIDERS).length} configured
          </span>
        </div>

        {/* Info banner */}
        <div className="mx-6 mt-4 mb-2 flex items-start gap-2 px-3 py-2.5 rounded-xl bg-secondary/40 border border-border/40 text-xs text-muted-foreground">
          <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>
            Keys are stored server-side and used by all pipelines.{" "}
            <strong className="text-foreground">Environment variable keys take priority</strong>{" "}
            (set via Replit Secrets) — those show as "Set via env" and cannot be overridden here.
          </span>
        </div>

        {/* Provider list */}
        {loading ? (
          <div className="flex items-center gap-2 text-muted-foreground text-sm py-12 justify-center">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading provider status…
          </div>
        ) : (
          <div className="px-6 pb-6 pt-2 space-y-4">
            {Object.entries(PROVIDERS).map(([id, info]) => {
              const status = statuses[id];
              const isConfigured = status?.configured ?? false;
              const isEnvKey = status?.source === "env";
              const currentInput = keyInputs[id] ?? "";

              return (
                <div
                  key={id}
                  className="rounded-xl border border-border/50 bg-secondary/20 overflow-hidden"
                >
                  {/* Provider header row */}
                  <div className="flex items-center justify-between px-4 py-3 border-b border-border/30">
                    <div className="flex items-center gap-3">
                      <Key className={cn("w-4 h-4", info.color)} />
                      <div>
                        <p className={cn("text-sm font-semibold", info.color)}>{info.label}</p>
                        <p className="text-xs text-muted-foreground">{info.description}</p>
                      </div>
                    </div>
                    {isConfigured ? (
                      <span className={cn(
                        "flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border font-medium",
                        "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                      )}>
                        <CheckCircle2 className="w-3 h-3" />
                        {isEnvKey ? "Set via env" : "Active"}
                      </span>
                    ) : (
                      <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-secondary text-muted-foreground border border-border font-medium">
                        <XCircle className="w-3 h-3" />
                        Not set
                      </span>
                    )}
                  </div>

                  {/* Body */}
                  <div className="px-4 py-3 space-y-3">
                    {/* Env var source — readonly, no action */}
                    {isEnvKey ? (
                      <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-500/5 border border-emerald-500/15 text-xs text-muted-foreground">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                        Key is provided via the{" "}
                        <code className="font-mono px-1 bg-secondary rounded">{info.envVar}</code>{" "}
                        environment variable — no action needed.
                      </div>
                    ) : (
                      <>
                        {/* Stored key display + remove */}
                        {isConfigured && status?.masked && (
                          <div className="flex items-center justify-between px-3 py-2 rounded-lg bg-secondary/60 border border-border/40">
                            <div className="flex items-center gap-2">
                              <Key className="w-3 h-3 text-muted-foreground" />
                              <span className="text-xs font-mono text-muted-foreground tracking-widest">
                                {status.masked}
                              </span>
                            </div>
                            <button
                              onClick={() => handleRemove(id)}
                              disabled={removing === id}
                              className="flex items-center gap-1.5 text-xs text-red-400 hover:text-red-300 transition-colors disabled:opacity-50"
                            >
                              {removing === id ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                <Trash2 className="w-3 h-3" />
                              )}
                              Remove
                            </button>
                          </div>
                        )}

                        {/* Key input */}
                        <div className="flex gap-2">
                          <div className="relative flex-1">
                            <input
                              type={visible[id] ? "text" : "password"}
                              value={currentInput}
                              onChange={(e) =>
                                setKeyInputs((prev) => ({ ...prev, [id]: e.target.value }))
                              }
                              placeholder={
                                isConfigured
                                  ? "Paste new key to replace…"
                                  : `Paste your ${info.envVar} here`
                              }
                              className={cn(inputCls, "pr-10")}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && currentInput) handleSave(id);
                              }}
                            />
                            <button
                              type="button"
                              onClick={() =>
                                setVisible((v) => ({ ...v, [id]: !v[id] }))
                              }
                              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                            >
                              {visible[id] ? (
                                <EyeOff className="w-3.5 h-3.5" />
                              ) : (
                                <Eye className="w-3.5 h-3.5" />
                              )}
                            </button>
                          </div>
                          <button
                            onClick={() => handleSave(id)}
                            disabled={!currentInput.trim() || saving === id}
                            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {saving === id ? (
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                              <Save className="w-3.5 h-3.5" />
                            )}
                            Save
                          </button>
                        </div>
                      </>
                    )}

                    {/* Models list */}
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs text-muted-foreground">Models:</span>
                      {info.models.map((m) => (
                        <span
                          key={m}
                          className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground"
                        >
                          {m}
                        </span>
                      ))}
                    </div>

                    {/* Docs link */}
                    <a
                      href={info.docsUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline"
                    >
                      <ExternalLink className="w-3 h-3" />
                      Get your key at {info.docsLabel}
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Tip */}
      <div className="flex items-start gap-2 px-4 py-3 rounded-xl bg-secondary/30 border border-border/40 text-xs text-muted-foreground">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          After saving a key, go to{" "}
          <strong className="text-foreground">Content Generation</strong> to enable AI mode on
          your fields and pick a model. Keys take effect immediately — no restart required.
        </span>
      </div>
    </div>
  );
}
