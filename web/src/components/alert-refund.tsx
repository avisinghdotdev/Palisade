import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, LoaderCircle } from "lucide-react";

import type { AlertRefund, Dispute } from "@/api/client";

import {
  disputeActivityQueryKey,
  disputeAlertOptions,
  disputeAlertQueryKey,
  disputeQueryKey,
  orderQueryKey,
  refundAlertMutation,
} from "@/api/client/@tanstack/react-query.gen";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { formatUtc } from "@/money";
import { errorDetail } from "@/shopper";

// The alert's window runs out by itself; the page reads where it stands this often.
const ALERT_REFRESH_MS = 15_000;

/** One line on where the refund stands. */
function standing(alert: AlertRefund): string {
  if (alert.refunded && alert.refund !== null) {
    return `Refunded in full ${formatUtc(alert.refund.finished_at ?? alert.refund.started_at)}.`;
  }
  if (alert.scheduled_for !== null) {
    return `Palisade refunds in full by itself at ${formatUtc(alert.scheduled_for)}, unless the alert closes first.`;
  }
  if (alert.can_refund) {
    return alert.filing_mode === "review"
      ? "Review and file is on, so nothing runs by itself: refund now, or answer the dispute with evidence."
      : "No refund is scheduled; refund now to avoid the chargeback.";
  }
  return "Nothing to refund now: PayPal has closed the alert, or the payment is no longer captured.";
}

/** A pre-chargeback alert: PayPal's deadline, Palisade's timer, and the merchant's refund. */
export function AlertRefundPanel({ dispute }: { dispute: Dispute }) {
  const path = { dispute_id: dispute.id };
  const queryClient = useQueryClient();
  const alert = useQuery({
    ...disputeAlertOptions({ path }),
    refetchInterval: dispute.status === "RESOLVED" ? false : ALERT_REFRESH_MS,
  });
  const refund = useMutation({
    ...refundAlertMutation(),
    onSuccess: (changed) => {
      queryClient.setQueryData(disputeAlertQueryKey({ path }), changed);
      void queryClient.invalidateQueries({
        queryKey: disputeQueryKey({ path }),
      });
      void queryClient.invalidateQueries({
        queryKey: disputeActivityQueryKey({ path }),
      });
      void queryClient.invalidateQueries({
        queryKey: orderQueryKey({ path: { number: dispute.order_number } }),
      });
    },
    onError: () => {
      void alert.refetch();
    },
  });
  const data = alert.data;
  const refused =
    data?.refund !== null &&
    data?.refund !== undefined &&
    !data.refunded &&
    data.refund.status_code !== null;

  return (
    <section
      aria-labelledby="alert"
      className="rounded-xl border border-amber-600/40 bg-amber-50 px-4 py-3 text-amber-950"
    >
      <h2
        className="flex items-center gap-2 text-base font-semibold"
        id="alert"
      >
        <BellRing aria-hidden className="size-4" />
        Pre-Chargeback Alert
      </h2>
      {alert.isPending && (
        <p className="mt-2 text-sm" role="status">
          Loading the alert…
        </p>
      )}
      {alert.isError && (
        <p className="mt-2 text-sm text-destructive">The alert did not load.</p>
      )}
      {data !== undefined && (
        <>
          <p className="mt-2 text-sm">
            The buyer asked their card issuer for a refund. A full refund before{" "}
            <time className="font-medium" dateTime={data.deadline}>
              {formatUtc(data.deadline)}
            </time>{" "}
            avoids the chargeback and its fees.
          </p>
          <p className="mt-2 text-sm" role="status">
            {standing(data)}
          </p>
          {data.refund !== null && (
            <p className="mt-2 font-mono text-xs break-all">
              PayPal-Request-Id {data.refund.request_id}
              {data.refund.status_code !== null &&
                ` · HTTP ${String(data.refund.status_code)}`}
              {data.refund.debug_id !== null &&
                ` · debug id ${data.refund.debug_id}`}
            </p>
          )}
          {refused && (
            <p className="mt-2 text-sm text-destructive">
              PayPal refused the last refund; trying again repeats the same
              request.
            </p>
          )}
          {data.can_refund && (
            <Dialog
              onOpenChange={(open) => {
                if (open) refund.reset();
              }}
            >
              <DialogTrigger asChild>
                <Button className="mt-3" size="sm">
                  Refund Now
                </Button>
              </DialogTrigger>
              {/* The dialog renders outside the page, so it takes the shop's theme itself. */}
              <DialogContent className="theme-shop">
                <DialogHeader>
                  <DialogTitle>
                    Refund Order {dispute.order_number} in Full?
                  </DialogTitle>
                  <DialogDescription>
                    PayPal returns the whole payment to the buyer. A refund
                    cannot be undone. It ends the alert before it becomes a
                    chargeback.
                  </DialogDescription>
                </DialogHeader>
                {refund.isPending && (
                  <p
                    className="flex items-center gap-2 text-sm text-muted-foreground"
                    role="status"
                  >
                    <LoaderCircle
                      aria-hidden
                      className="size-4 motion-safe:animate-spin"
                    />
                    Refunding through PayPal…
                  </p>
                )}
                {refund.isError && (
                  <p className="text-sm text-destructive" role="alert">
                    {errorDetail(refund.error)}
                  </p>
                )}
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="outline">Keep the Payment</Button>
                  </DialogClose>
                  <Button
                    disabled={refund.isPending || refund.isSuccess}
                    onClick={() => {
                      refund.mutate({ path });
                    }}
                  >
                    {refund.isSuccess ? "Refunded" : "Refund in Full"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          )}
        </>
      )}
    </section>
  );
}
