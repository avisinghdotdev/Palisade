import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { ledgerOptions } from "@/api/client/@tanstack/react-query.gen";
import { LedgerGrid } from "@/components/ledger-grid";
import { UnclaimedPayments } from "@/components/ledger-unclaimed";
import { Button } from "@/components/ui/button";
import { ledgerSummary } from "@/ledger-labels";
import { pageTitle } from "@/page-title";

import type { Route } from "./+types/console-ledger";

// Every read calls PayPal, and PayPal's ledger runs hours behind, so coming back to the page
// within this time shows the last read; the button reads again.
const FRESH_FOR_MS = 5 * 60_000;

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("PayPal Ledger", "Console") },
];

export default function ConsoleLedger() {
  const ledger = useQuery({ ...ledgerOptions(), staleTime: FRESH_FOR_MS });

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            PayPal Ledger
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Every paid order beside the row PayPal&apos;s own transaction ledger
            holds for its payment: the amount, the status, the custom ID, and
            any refund. Below it, any payment PayPal lists that no paid order
            names. PayPal adds a payment to its ledger within three hours.
          </p>
        </div>
        <Button
          disabled={ledger.isFetching}
          onClick={() => {
            void ledger.refetch();
          }}
          size="sm"
          variant="outline"
        >
          <RefreshCw
            aria-hidden
            className={ledger.isFetching ? "animate-spin" : undefined}
          />
          Check Again
        </Button>
      </div>
      {ledger.isError && (
        <p className="mt-4 text-sm text-destructive">
          PayPal did not return its ledger. Check again in a moment.
        </p>
      )}
      {ledger.data && (
        <p className="mt-4 text-sm font-medium">{ledgerSummary(ledger.data)}</p>
      )}
      <div className="mt-6">
        <LedgerGrid
          entries={ledger.data?.orders ?? []}
          failed={ledger.isError}
          loading={ledger.isPending}
        />
      </div>
      {ledger.data !== undefined && ledger.data.unclaimed.length > 0 && (
        <UnclaimedPayments payments={ledger.data.unclaimed} />
      )}
    </>
  );
}
