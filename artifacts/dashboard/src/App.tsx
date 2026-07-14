import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Layout } from "@/components/Layout";
import NotFound from "@/pages/not-found";

// Pages
import Dashboard from "./pages/Dashboard";
import Products from "./pages/Products";
import Jobs from "./pages/Jobs";
import Stores from "./pages/Stores";
import Sunsky from "./pages/Sunsky";
import Sync from "./pages/Sync";
import ContentGeneration from "./pages/ContentGeneration";
import Pipeline from "./pages/Pipeline";
import Pipelines from "./pages/Pipelines";
import Settings from "./pages/Settings";

// Settings sub-pages
import SunskyCategories from "./pages/settings/SunskyCategories";
import WooCategories from "./pages/settings/WooCategories";
import CategoryMapping from "./pages/settings/CategoryMapping";
import AttributeMapping from "./pages/settings/AttributeMapping";
import AttributeProfiles from "./pages/settings/AttributeProfiles";
import ExtractionRules from "./pages/settings/ExtractionRules";
import InventoryMapping from "./pages/settings/InventoryMapping";
import ImagesSettings from "./pages/settings/ImagesSettings";
import PipelineDefaults from "./pages/settings/PipelineDefaults";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function Router() {
  return (
    <Layout>
      <Switch>
        <Route path="/" component={Dashboard} />

        {/* Pipelines */}
        <Route path="/pipelines" component={Pipelines} />
        <Route path="/pipelines/products" component={Products} />
        <Route path="/pipelines/new" component={Pipeline} />

        {/* Settings — Connections */}
        <Route path="/settings/stores" component={Stores} />
        <Route path="/settings/ai-keys" component={Settings} />

        {/* Settings — Mapping & Rules */}
        <Route path="/settings/sunsky-categories" component={SunskyCategories} />
        <Route path="/settings/woo-categories" component={WooCategories} />
        <Route path="/settings/category-mapping" component={CategoryMapping} />
        <Route path="/settings/attribute-mapping" component={AttributeMapping} />
        <Route path="/settings/attribute-profiles" component={AttributeProfiles} />
        <Route path="/settings/extraction-rules" component={ExtractionRules} />
        <Route path="/settings/inventory-mapping" component={InventoryMapping} />

        {/* Settings — Pipeline Defaults */}
        <Route path="/content" component={ContentGeneration} />
        <Route path="/settings/images" component={ImagesSettings} />
        <Route path="/settings/pipeline-defaults" component={PipelineDefaults} />

        {/* Legacy routes (kept for backward compat) */}
        <Route path="/products" component={Products} />
        <Route path="/jobs" component={Jobs} />
        <Route path="/stores" component={Stores} />
        <Route path="/sunsky" component={Sunsky} />
        <Route path="/sync" component={Sync} />
        <Route path="/pipeline" component={Pipeline} />
        <Route path="/settings" component={Settings} />

        <Route component={NotFound} />
      </Switch>
    </Layout>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
