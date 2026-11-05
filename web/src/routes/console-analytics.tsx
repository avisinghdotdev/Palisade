import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { lazy, Suspense } from "react";

import { analyticsOptions } from "@/api/client/@tanstack/react-query.gen";
import { Button } from "@/components/ui/button";
import { useHydrated } from "@/hooks/use-hydrated";
import { pageTitle } from "@/page-title";

import type { Route } from "./+types/console-analytics";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Analytics", "Console") },
];

// AG Studio is several megabytes, so it loads only when this page opens, never in the prerender.
const AnalyticsDashboard = lazy(() =>
  import("@/components/analytics-dashboard").then((module) => ({
    default: module.AnalyticsDashboard,
  })),
);

/** Dispute rate, win rate, and time to evidence, for agent and human orders side by side. */
export default function ConsoleAnalytics() {
  const analytics = useQuery(analyticsOptions());
  const hydrated = useHydrated();

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            How often buyers dispute the store&apos;s paid orders, how fast
            Palisade answers, and how PayPal decides, for orders a shopping
            agent placed and orders a person placed. Built with AG Studio from
            every order and dispute the store holds.
          </p>
        </div>
        <Button
          disabled={analytics.isFetching}
          onClick={() => {
            void analytics.refetch();
          }}
          size="sm"
          variant="outline"
        >
          <RefreshCw
            aria-hidden
            className={analytics.isFetching ? "animate-spin" : undefined}
          />
          Refresh
        </Button>
      </div>
      <div className="mt-6">
        {analytics.isPending && (
          <p className="text-sm text-muted-foreground" role="status">
            Loading the analytics…
          </p>
        )}
        {analytics.isError && (
          <p className="text-sm text-destructive">
            The analytics did not load. Refresh to try again.
          </p>
        )}
        {hydrated && analytics.data !== undefined && (
          <Suspense
            fallback={
              <p className="text-sm text-muted-foreground" role="status">
                Loading the dashboard…
              </p>
            }
          >
            <AnalyticsDashboard rows={analytics.data} />
          </Suspense>
        )}
      </div>
    </>
  );
}
