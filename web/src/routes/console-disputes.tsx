import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { disputesOptions } from "@/api/client/@tanstack/react-query.gen";
import { DisputesGrid } from "@/components/disputes-grid";
import { FilingMode } from "@/components/filing-mode";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/page-title";

import type { Route } from "./+types/console-disputes";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Disputes", "Console") },
];

export default function ConsoleDisputes() {
  const disputes = useQuery(disputesOptions());

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Disputes</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every dispute PayPal opened on the store&apos;s orders, joined to
            the order and the buyer&apos;s signed mandate, and answered with the
            record of how the order was placed.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <FilingMode />
          <Button
            disabled={disputes.isFetching}
            onClick={() => {
              void disputes.refetch();
            }}
            size="sm"
            variant="outline"
          >
            <RefreshCw
              aria-hidden
              className={disputes.isFetching ? "animate-spin" : undefined}
            />
            Refresh
          </Button>
        </div>
      </div>
      {disputes.isError && (
        <p className="mt-4 text-sm text-destructive">
          The disputes did not load. Refresh to try again.
        </p>
      )}
      <div className="mt-6">
        <DisputesGrid
          disputes={disputes.data ?? []}
          failed={disputes.isError}
          loading={disputes.isPending}
        />
      </div>
    </>
  );
}
