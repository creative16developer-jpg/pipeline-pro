import { Link, useLocation, useSearch } from "wouter";
import { motion } from "framer-motion";
import {
  LayoutDashboard,
  PackageSearch,
  Activity,
  Store,
  Zap,
  Settings,
  FileSpreadsheet,
  Key,
  Star,
  ArrowRightLeft,
  Tags,
  List,
  Layers,
  SlidersHorizontal,
  Sparkles,
  Image,
  Wrench,
  Bell,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ─── Nav item types ────────────────────────────────────────────────────────────
interface NavGroup {
  label?: string;
  items: NavItem[];
}

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  /** when set, item is active if path === /settings AND ?tab matches */
  matchTab?: string;
}

// ─── Nav structure (mirrors client HTML reference) ────────────────────────────
const NAV_GROUPS: NavGroup[] = [
  {
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard },
    ],
  },
  {
    label: "Pipelines",
    items: [
      { href: "/pipelines",          label: "All Runs",              icon: Activity },
      { href: "/pipeline",           label: "New Pipeline — Sunsky", icon: Zap },
      { href: "/pipeline?source=csv",label: "New Pipeline — CSV",    icon: FileSpreadsheet },
      { href: "/products",           label: "Products",              icon: PackageSearch },
    ],
  },
  {
    label: "Settings — Connections",
    items: [
      { href: "/stores",                  label: "Stores",             icon: Store },
      { href: "/settings?tab=keys",       label: "AI Provider Keys",   icon: Key,            matchTab: "keys" },
    ],
  },
  {
    label: "Settings — Mapping",
    items: [
      { href: "/settings?tab=sunsky-cats",  label: "Sunsky Categories",    icon: Star,             matchTab: "sunsky-cats"  },
      { href: "/settings?tab=woo-cats",     label: "WooCommerce Categories",icon: Star,             matchTab: "woo-cats"     },
      { href: "/settings?tab=mappings",     label: "Category Mapping",     icon: ArrowRightLeft,   matchTab: "mappings"     },
      { href: "/settings?tab=attr-mapping", label: "Attribute Mapping",    icon: Tags,             matchTab: "attr-mapping" },
      { href: "/settings?tab=profiles",     label: "Attribute Profiles",   icon: Layers,           matchTab: "profiles"     },
      { href: "/settings?tab=rules",        label: "Extraction Rules",     icon: List,             matchTab: "rules"        },
      { href: "/settings?tab=inventory",    label: "Inventory Mapping",    icon: SlidersHorizontal,matchTab: "inventory"    },
    ],
  },
  {
    label: "Settings — Defaults",
    items: [
      { href: "/settings?tab=content-gen",      label: "Content Generation", icon: Sparkles, matchTab: "content-gen"      },
      { href: "/settings?tab=images",           label: "Images",             icon: Image,    matchTab: "images"           },
      { href: "/settings?tab=pipeline-defaults",label: "Pipeline Defaults",  icon: Wrench,   matchTab: "pipeline-defaults"},
    ],
  },
];

// ─── Layout ────────────────────────────────────────────────────────────────────
export function Layout({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const search = useSearch();
  const searchParams = new URLSearchParams(search);
  const currentTab = searchParams.get("tab") ?? "";

  function isActive(item: NavItem): boolean {
    if (item.matchTab) {
      return location === "/settings" && currentTab === item.matchTab;
    }
    // For /pipeline (sunsky) — active when on /pipeline without source=csv
    if (item.href === "/pipeline") {
      return location === "/pipeline" && searchParams.get("source") !== "csv";
    }
    // For /pipeline?source=csv — active when on /pipeline with source=csv
    if (item.href === "/pipeline?source=csv") {
      return location === "/pipeline" && searchParams.get("source") === "csv";
    }
    // Default: path match
    return location === item.href;
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col md:flex-row overflow-hidden">
      {/* Sidebar */}
      <aside className="w-full md:w-56 border-r border-border/50 bg-card/40 backdrop-blur-xl flex-shrink-0 z-20 flex flex-col h-auto md:h-screen sticky top-0">
        {/* Logo */}
        <div className="px-4 py-4 border-b border-border/50 flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary to-violet-600 flex items-center justify-center shadow-lg shadow-primary/20">
            <PackageSearch className="w-4 h-4 text-white" />
          </div>
          <h1 className="font-display font-bold text-base tracking-wide text-white">
            Pipeline<span className="text-primary">Pro</span>
          </h1>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto overflow-x-hidden py-2">
          {NAV_GROUPS.map((group, gi) => (
            <div key={gi} className={cn("pb-1", gi > 0 && "pt-1")}>
              {group.label && (
                <p className="px-4 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60">
                  {group.label}
                </p>
              )}
              {group.items.map((item) => {
                const active = isActive(item);
                const Icon = item.icon;
                return (
                  <Link key={item.href} href={item.href}>
                    <div className={cn(
                      "relative flex items-center gap-2.5 mx-2 px-3 py-2 rounded-lg text-sm transition-all duration-200 cursor-pointer group",
                      active
                        ? "text-primary bg-primary/10 border-l-2 border-primary font-medium"
                        : "text-muted-foreground hover:text-foreground hover:bg-secondary/50 border-l-2 border-transparent"
                    )}>
                      {active && (
                        <motion.div
                          layoutId="nav-active-bg"
                          className="absolute inset-0 rounded-lg bg-primary/5"
                          transition={{ type: "spring", stiffness: 400, damping: 35 }}
                        />
                      )}
                      <Icon className={cn("w-4 h-4 shrink-0 relative z-10", active ? "text-primary" : "")} />
                      <span className="relative z-10 whitespace-nowrap text-[13px] leading-snug">
                        {item.label}
                      </span>
                    </div>
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col h-screen overflow-hidden relative">
        <header className="h-14 flex items-center justify-end px-8 border-b border-border/30 bg-background/50 backdrop-blur-md sticky top-0 z-10 shrink-0">
          <div className="flex items-center gap-4">
            <button className="p-2 rounded-full hover:bg-secondary text-muted-foreground transition-colors relative">
              <Bell className="w-4 h-4" />
              <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-primary rounded-full ring-2 ring-background" />
            </button>
            <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center border border-border">
              <span className="text-[11px] font-bold text-muted-foreground">AD</span>
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-4 md:p-8 relative">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
            className="max-w-5xl mx-auto pb-20"
          >
            {children}
          </motion.div>
        </div>
      </main>
    </div>
  );
}
