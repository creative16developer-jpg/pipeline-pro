import { Cpu, Construction } from "lucide-react";

export default function PipelineDefaults() {
  return <SettingsStub title="Pipeline Options" icon={Cpu} description="Set global pipeline defaults: auto review pause, AI enrichment toggle, image compression, force re-run behaviour, and per-store concurrency limits." />;
}

function SettingsStub({ title, icon: Icon, description }: { title: string; icon: any; description: string }) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-display font-bold text-foreground">{title}</h1>
        <p className="text-muted-foreground mt-1 text-sm">{description}</p>
      </div>
      <div className="bg-card border border-border/50 rounded-2xl p-12 flex flex-col items-center justify-center gap-4 text-center">
        <div className="w-14 h-14 rounded-2xl bg-primary/10 flex items-center justify-center">
          <Icon className="w-7 h-7 text-primary" />
        </div>
        <div>
          <p className="font-semibold text-foreground">Coming Soon</p>
          <p className="text-sm text-muted-foreground mt-1 max-w-sm">{title} configuration will be available in a future update.</p>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground/50 mt-2">
          <Construction className="w-3.5 h-3.5" />
          Under development
        </div>
      </div>
    </div>
  );
}
