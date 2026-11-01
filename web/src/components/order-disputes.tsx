import { useQuery } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { Link } from "react-router";

import type { Order } from "@/api/client";

import { disputesOptions } from "@/api/client/@tanstack/react-query.gen";
import { DisputeStatus } from "@/components/dispute-status";
import { FileDisputeDialog } from "@/components/file-dispute-dialog";
import { REASON_LABELS } from "@/dispute-labels";

/** The disputes PayPal opened on the order; on a paid demo-buyer order, the way to open one. */
export function OrderDisputes({ order }: { order: Order }) {
  const disputes = useQuery(
    disputesOptions({ query: { order_number: order.number } }),
  );

  return (
    <section
      aria-labelledby="disputes"
      className="rounded-xl border bg-card px-5 py-4"
    >
      <h2
        className="flex items-center gap-2 py-1 text-base font-semibold"
        id="disputes"
      >
        <Scale aria-hidden className="size-4 text-muted-foreground" />
        Disputes
      </h2>
      {disputes.isPending && (
        <p className="mt-2 text-sm text-muted-foreground" role="status">
          Loading disputes…
        </p>
      )}
      {disputes.isError && (
        <p className="mt-2 text-sm text-destructive">
          The disputes did not load.
        </p>
      )}
      {disputes.data?.length === 0 && (
        <p className="mt-2 text-sm text-muted-foreground">
          No dispute on this order.
        </p>
      )}
      {disputes.data !== undefined && disputes.data.length > 0 && (
        <ul className="mt-1 divide-y">
          {disputes.data.map((dispute) => (
            <li className="py-3" key={dispute.id}>
              <Link
                className="font-mono text-sm font-medium text-primary underline-offset-2 hover:underline"
                to={`/console/disputes/${dispute.id}`}
              >
                {dispute.id}
              </Link>
              <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                {REASON_LABELS[dispute.reason]}
                <DisputeStatus
                  outcome={dispute.outcome}
                  status={dispute.status}
                />
              </p>
            </li>
          ))}
        </ul>
      )}
      {order.status === "captured" &&
        (order.wallet_demo ? (
          <FileDisputeDialog number={order.number} />
        ) : (
          <p className="mt-2 text-xs text-muted-foreground">
            Only the demo buyer&apos;s orders can be disputed from here. A buyer
            on their own sandbox account files from Activity on
            sandbox.paypal.com.
          </p>
        ))}
    </section>
  );
}
