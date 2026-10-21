import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { webhookEventsOptions } from "@/api/client/@tanstack/react-query.gen";
import { AboutDialog } from "@/components/about-dialog";
import { ConsoleLogo } from "@/components/console-logo";
import { PalisadeBadge } from "@/components/palisade-badge";
import { ShopHeader } from "@/components/shop-header";
import { ShopLogo } from "@/components/shop-logo";
import { SurfaceLink } from "@/components/surface-link";
import { Button } from "@/components/ui/button";
import { WebhookEventsGrid } from "@/components/webhook-events-grid";

import type { Route } from "./+types/merchant";

export const links: Route.LinksFunction = () => [
  { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
];

export const meta: Route.MetaFunction = () => [{ title: "Merchant console" }];

export default function Merchant() {
  const events = useQuery(webhookEventsOptions());

  return (
    <div className="theme-shop flex min-h-dvh flex-col bg-background text-foreground">
      <ShopHeader
        beside={
          <>
            <span aria-hidden className="h-5 w-px shrink-0 bg-border" />
            <span className="flex items-center gap-1.5 text-sm whitespace-nowrap text-muted-foreground">
              <ConsoleLogo className="size-4 shrink-0" />
              Merchant console
            </span>
          </>
        }
        to="/merchant"
      >
        <PalisadeBadge>Powered by Palisade</PalisadeBadge>
        <SurfaceLink icon={<ShopLogo />} to="/shop">
          Shop
        </SurfaceLink>
        <AboutDialog title="About Console">
          <p>
            This is the merchant's side of Palisade for Ridgeline Running: every
            PayPal event the store receives, and in later milestones every agent
            order, mandate, policy decision, and dispute with the evidence
            Palisade filed.
          </p>
        </AboutDialog>
      </ShopHeader>
      <main className="flex-1 px-4 py-8 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Every PayPal event this store received.
            </p>
          </div>
          <Button
            disabled={events.isFetching}
            onClick={() => {
              void events.refetch();
            }}
            size="sm"
            variant="outline"
          >
            <RefreshCw
              aria-hidden
              className={events.isFetching ? "animate-spin" : undefined}
            />
            Refresh
          </Button>
        </div>
        {events.isError && (
          <p className="mt-4 text-sm text-destructive">
            The events did not load. Refresh to try again.
          </p>
        )}
        <div className="mt-6">
          <WebhookEventsGrid
            events={events.data ?? []}
            failed={events.isError}
            loading={events.isPending}
          />
        </div>
      </main>
    </div>
  );
}
