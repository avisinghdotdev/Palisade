import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { webhookEventsOptions } from "@/api/client/@tanstack/react-query.gen";
import { Button } from "@/components/ui/button";
import { WebhookEventsGrid } from "@/components/webhook-events-grid";
import { pageTitle } from "@/page-title";

import type { Route } from "./+types/console-activity";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Activity", "Console") },
];

export default function ConsoleActivity() {
  const events = useQuery(webhookEventsOptions());

  return (
    <>
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
    </>
  );
}
