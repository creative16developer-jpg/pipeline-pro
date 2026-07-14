import { Link, useLocation } from "wouter";
import { motion } from "framer-motion";
import {
  LayoutDashboard,
  PackageSearch,
  Activity,
  Store,
  Sparkles,
  Zap,
  Settings,
  ChevronDown,
  ChevronRight,
  Image,
  Tag,
  ArrowLeftRight,
  Layers,
  Filter,
  BarChart3,
  Cpu,
  KeyRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { useDashboardStats } from "@/hooks/use-dashboard";

const PIPELINES_ITEMS = [
  { href: "/pipelines", label: "All Runs", exact: true },
  { href: "/pipelines/products", label: "Products" },
];

const SETTINGS_GROUPS = [
  {
    label: "Connections",
    items: [
      { href: "/settings/stores", label: "Stores", icon: Store },
      { href: "/settings/ai-keys", label: "AI Provider Keys", icon: KeyRound },
    ],
  },
  {
    label: "Mapping & Rules",
    items: [
      { href: "/settings/sunsky-categories", label: "Sunsky Categories", icon: Tag },
      { href: "/settings/woo-categories", label: "WooCommerce Categories", icon: Tag },
      { href: "/settings/category-mapping", label: "Category Mapping", icon: ArrowLeftRight },
      { href: "/settings/attribute-mapping", label: "Attribute Mapping", icon: Layers },
      { href: "/settings/attribute-profiles", label: "Attribute Profiles", icon: Filter },
      { href: "/settings/extraction-rules", label: "Extraction Rules", icon: Filter },
      { href: "/settings/inventory-mapping", label: "Inventory Mapping", icon: BarChart3 },
    ],
  },
  {
    label: "Pipeline Defaults",
    items: [
      { href: "/content", label: "Content Generation", icon: Sparkles },
      { href: "/settings/images", label: "Images", icon: Image },
      { href: "/settings/pipeline-defaults", label: "Pipeline Options", icon: Cpu },
    ],
  },
];

function HealthDot() {
  const { data: stats, isError } = useDashboardStats();
  if (isError) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-rose-400">
        <span className="w-2 h-2 rounded-full bg-rose-500 animate-pulse" />
        System error
      </span>
    );
  }
  if (!stats) return null;
  if ((stats as any).waitingForInput > 0) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-amber-400">
        <span className="w-2 h-2 rounded-full bg-amber-500" />
        Needs attention
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-xs text-emerald-400">
      <span className="w-2 h-2 rounded-full bg-emerald-500" />
      All systems go
    </span>
  );
}

export function Layout({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const isSettingsActive =
    location.startsWith("/settings") || location === "/content";
  const [settingsOpen, setSettingsOpen] = useState(() => isSettingsActive);

  function isActive(href: string, exact = false) {
    if (exact) return location === href;
    return location === href || location.startsWith(href + "/");
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col md:flex-row overflow-hidden">
      {/* Sidebar */}
      <aside className="w-full md:w-60 border-r border-border/50 bg-card/40 backdrop-blur-xl flex-shrink-0 z-20 flex flex-col md:h-screen sticky top-0">
        {/* Logo */}
        <div className="p-5 flex items-center gap-3 border-b border-border/30 shrink-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-violet-600 flex items-center justify-center shadow-lg shadow-primary/20">
            <PackageSearch className="w-4 h-4 text-white" />
          </div>
          <h1 className="font-display font-bold text-base tracking-wide text-white">
            Pipeline<span className="text-primary">Pro</span>
          </h1>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-3 space-y-0.5 text-sm">
          {/* Dashboard */}
          <NavItem href="/" label="Dashboard" icon={LayoutDashboard} active={location === "/"} />

          {/* Pipelines section */}
          <div className="pt-3 pb-1">
            <p className="px-3 text-[10px] font-semibold tracking-widest text-muted-foreground/50 uppercase mb-1">
              Pipelines
            </p>
            {PIPELINES_ITEMS.map((item) => (
              <SubNavItem
                key={item.href}
                href={item.href}
                label={item.label}
                active={isActive(item.href, item.exact)}
              />
            ))}
          </div>

          {/* Settings section (collapsible) */}
          <div className="pt-3">
            <button
              onClick={() => setSettingsOpen((v) => !v)}
              className={cn(
                "w-full flex items-center justify-between px-3 py-1.5 rounded-lg transition-colors",
                isSettingsActive
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
              )}
            >
              <span className="text-[10px] font-semibold tracking-widest uppercase flex items-center gap-1.5">
                <Settings className="w-3.5 h-3.5" />
                Settings
              </span>
              {settingsOpen ? (
                <ChevronDown className="w-3.5 h-3.5" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5" />
              )}
            </button>

            {settingsOpen && (
              <div className="mt-1 space-y-3">
                {SETTINGS_GROUPS.map((group) => (
                  <div key={group.label}>
                    <p className="px-3 text-[10px] font-medium text-muted-foreground/40 uppercase tracking-wider mb-0.5">
                      {group.label}
                    </p>
                    {group.items.map((item) => (
                      <SubNavItem
                        key={item.href}
                        href={item.href}
                        label={item.label}
                        icon={item.icon}
                        active={isActive(item.href, true)}
                      />
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        </nav>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col md:h-screen overflow-hidden relative">
        {/* Header */}
        <header className="h-14 flex items-center justify-between px-6 border-b border-border/30 bg-background/50 backdrop-blur-md sticky top-0 z-10 shrink-0">
          <HealthDot />
          <Link href="/pipelines/new">
            <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:bg-primary/90 transition-all shadow-[0_0_15px_rgba(99,102,241,0.3)] hover:shadow-[0_0_20px_rgba(99,102,241,0.5)] hover:-translate-y-0.5">
              <Zap className="w-3.5 h-3.5" />
              New Pipeline
            </button>
          </Link>
        </header>

        <div className="flex-1 overflow-y-auto p-5 md:p-8">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35 }}
            className="max-w-7xl mx-auto pb-16"
          >
            {children}
          </motion.div>
        </div>
      </main>
    </div>
  );
}

function NavItem({
  href,
  label,
  icon: Icon,
  active,
}: {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  active: boolean;
}) {
  return (
    <Link href={href}>
      <div
        className={cn(
          "flex items-center gap-2.5 px-3 py-2 rounded-lg transition-all duration-200 relative group cursor-pointer",
          active
            ? "text-primary-foreground bg-primary/10 border border-primary/20"
            : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
        )}
      >
        {active && (
          <motion.div
            layoutId="nav-active"
            className="absolute inset-0 bg-primary/10 border border-primary/20 rounded-lg"
            transition={{ type: "spring", stiffness: 350, damping: 35 }}
          />
        )}
        <Icon className={cn("w-4 h-4 relative z-10 shrink-0", active ? "text-primary" : "")} />
        <span className="font-medium relative z-10">{label}</span>
      </div>
    </Link>
  );
}

function SubNavItem({
  href,
  label,
  icon: Icon,
  active,
}: {
  href: string;
  label: string;
  icon?: React.ComponentType<{ className?: string }>;
  active: boolean;
}) {
  return (
    <Link href={href}>
      <div
        className={cn(
          "flex items-center gap-2 pl-3 pr-3 py-1.5 rounded-lg transition-colors cursor-pointer",
          active
            ? "text-primary bg-primary/10"
            : "text-muted-foreground hover:text-foreground hover:bg-secondary/30"
        )}
      >
        {Icon ? (
          <Icon className="w-3.5 h-3.5 shrink-0 opacity-70" />
        ) : (
          <span className="w-3.5 h-3.5 shrink-0 flex items-center justify-center">
            <span className={cn("w-1 h-1 rounded-full", active ? "bg-primary" : "bg-muted-foreground/40")} />
          </span>
        )}
        <span className="text-xs font-medium truncate">{label}</span>
      </div>
    </Link>
  );
}
