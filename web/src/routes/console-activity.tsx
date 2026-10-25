import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";

import type { Activity } from "@/api/client";

import { ACTIVITY_LABELS } from "@/activity-labels";
import {
  activityOptions,
  webhookEventsOptions,
} from "@/api/client/@tanstack/react-query.gen";
import { ActivityGrid } from "@/components/activity-grid";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { WebhookEventsGrid } from "@/components/webhook-events-grid";
import { pageTitle } from "@/page-title";

import type { Route } from "./+types/console-activity";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Activity", "Console") },
];

const ALL_KINDS = "all";
const KINDS = Object.keys(ACTIVITY_LABELS) as Activity["kind"][];

function RefreshButton({
  fetching,
  onClick,
}: {
  fetching: boolean;
  onClick: () => void;
}) {
  return (
    <Button disabled={fetching} onClick={onClick} size="sm" variant="outline">
      <RefreshCw
        aria-hidden
        className={fetching ? "animate-spin" : undefined}
      />
      Refresh
    </Button>
  );
}

export default function ConsoleActivity() {
  const [kind, setKind] = useState<Activity["kind"] | typeof ALL_KINDS>(
    ALL_KINDS,
  );
  const activity = useQuery(
    activityOptions({ query: kind === ALL_KINDS ? {} : { kind } }),
  );
  const events = useQuery(webhookEventsOptions());

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          What agents did at the store, and what PayPal reported, newest first.
        </p>
      </div>
      <Tabs className="mt-6" defaultValue="agents">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <TabsList>
            <TabsTrigger value="agents">Agent Activity</TabsTrigger>
            <TabsTrigger value="paypal">PayPal Events</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent className="mt-4" value="agents">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Select
              onValueChange={(value) => {
                setKind(value as Activity["kind"] | typeof ALL_KINDS);
              }}
              value={kind}
            >
              <SelectTrigger aria-label="Event kind" className="w-60" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_KINDS}>All events</SelectItem>
                {KINDS.map((option) => (
                  <SelectItem key={option} value={option}>
                    {ACTIVITY_LABELS[option]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <RefreshButton
              fetching={activity.isFetching}
              onClick={() => {
                void activity.refetch();
              }}
            />
          </div>
          {activity.isError && (
            <p className="mt-4 text-sm text-destructive">
              The activity did not load. Refresh to try again.
            </p>
          )}
          <div className="mt-4">
            <ActivityGrid
              failed={activity.isError}
              loading={activity.isPending}
              rows={activity.data ?? []}
            />
          </div>
        </TabsContent>
        <TabsContent className="mt-4" value="paypal">
          <div className="flex justify-end">
            <RefreshButton
              fetching={events.isFetching}
              onClick={() => {
                void events.refetch();
              }}
            />
          </div>
          {events.isError && (
            <p className="mt-4 text-sm text-destructive">
              The events did not load. Refresh to try again.
            </p>
          )}
          <div className="mt-4">
            <WebhookEventsGrid
              events={events.data ?? []}
              failed={events.isError}
              loading={events.isPending}
            />
          </div>
        </TabsContent>
      </Tabs>
    </>
  );
}
