import { Link, useLocation } from "wouter";
import { motion } from "framer-motion";
import { 
  LayoutDashboard, 
  PackageSearch, 
  Activity, 
  Store, 
  CloudDownload,
  Settings,
  Bell
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/products", label: "Products", icon: PackageSearch },
  { href: "/jobs", label: "Import Jobs", icon: Activity },
  { href: "/stores", label: "Stores", icon: Store },
  { href: "/sunsky", label: "Sunsky Fetch", icon: CloudDownload },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col md:flex-row overflow-hidden">
      {/* Sidebar */}
      <aside className="w-full md:w-64 border-r border-border/50 bg-card/40 backdrop-blur-xl flex-shrink-0 z-20 flex flex-col h-auto md:h-screen sticky top-0">
        <div className="p-6 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-violet-600 flex items-center justify-center shadow-lg shadow-primary/20">
            <PackageSearch className="w-5 h-5 text-white" />
          </div>
          <h1 className="font-display font-bold text-lg tracking-wide text-white">Pipeline<span className="text-primary">Pro</span></h1>
        </div>

        <nav className="flex-1 px-4 py-2 space-y-1 overflow-x-auto md:overflow-visible flex md:flex-col items-center md:items-stretch">
          {NAV_ITEMS.map((item) => {
            const isActive = location === item.href;
            const Icon = item.icon;
            
            return (
              <Link key={item.href} href={item.href} className="block">
                <div className={cn(
                  "flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300 relative group cursor-pointer",
                  isActive ? "text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                )}>
                  {isActive && (
                    <motion.div 
                      layoutId="nav-active"
                      className="absolute inset-0 bg-primary/10 border border-primary/20 rounded-xl"
                      transition={{ type: "spring", stiffness: 300, damping: 30 }}
                    />
                  )}
                  <Icon className={cn("w-5 h-5 relative z-10 transition-colors", isActive ? "text-primary" : "")} />
                  <span className="font-medium text-sm relative z-10 whitespace-nowrap">{item.label}</span>
                </div>
              </Link>
            );
          })}
        </nav>
        
        <div className="p-6 hidden md:block">
          <div className="flex items-center gap-3 px-4 py-3 rounded-xl text-muted-foreground hover:text-foreground hover:bg-secondary/50 transition-colors cursor-pointer">
            <Settings className="w-5 h-5" />
            <span className="font-medium text-sm">Settings</span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col h-screen overflow-hidden relative">
        <header className="h-16 flex items-center justify-end px-8 border-b border-border/30 bg-background/50 backdrop-blur-md sticky top-0 z-10 shrink-0">
           <div className="flex items-center gap-4">
             <button className="p-2 rounded-full hover:bg-secondary text-muted-foreground transition-colors relative">
               <Bell className="w-5 h-5" />
               <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-primary rounded-full ring-2 ring-background"></span>
             </button>
             <div className="w-8 h-8 rounded-full bg-secondary flex items-center justify-center border border-border">
               <span className="text-xs font-bold text-muted-foreground">AD</span>
             </div>
           </div>
        </header>

        <div className="flex-1 overflow-y-auto p-4 md:p-8 relative">
          <motion.div 
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            className="max-w-7xl mx-auto pb-20"
          >
            {children}
          </motion.div>
        </div>
      </main>
    </div>
  );
}
