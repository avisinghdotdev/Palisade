import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Truck } from "lucide-react";
import { useState } from "react";

import {
  orderQueryKey,
  shipOrderMutation,
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
import { errorDetail } from "@/shopper";

const CARRIER = "USPS";

/** A number shaped like a USPS one; PayPal's sandbox checks none, and no parcel exists. */
function sandboxTrackingNumber(): string {
  const digits = Array.from(
    crypto.getRandomValues(new Uint8Array(18)),
    (byte) => String(byte % 10),
  ).join("");
  return `9400${digits}`;
}

/** Marks a paid order shipped: the tracking goes onto its PayPal capture as dispute evidence. */
export function ShipOrderDialog({ number }: { number: string }) {
  const queryClient = useQueryClient();
  // A new number each time the dialog opens; the number shown is the number sent.
  const [trackingNumber, setTrackingNumber] = useState(sandboxTrackingNumber);
  const ship = useMutation({
    ...shipOrderMutation(),
    onSuccess: (order) => {
      queryClient.setQueryData(
        orderQueryKey({ path: { number: order.number } }),
        order,
      );
    },
  });

  return (
    <Dialog
      onOpenChange={(open) => {
        if (open) {
          setTrackingNumber(sandboxTrackingNumber());
          ship.reset();
        }
      }}
    >
      <DialogTrigger asChild>
        <Button className="mt-3 w-full" size="sm">
          <Truck aria-hidden />
          Mark as Shipped
        </Button>
      </DialogTrigger>
      {/* The dialog renders outside the page, so it takes the shop's theme itself. */}
      <DialogContent className="theme-shop">
        <DialogHeader>
          <DialogTitle>Mark Order {number} as Shipped</DialogTitle>
          <DialogDescription>
            The tracking goes onto this order&apos;s PayPal capture, so a
            dispute about delivery has proof to file.
          </DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-lg border bg-secondary/50 px-4 py-3 text-sm">
          <dt className="text-muted-foreground">Carrier</dt>
          <dd>{CARRIER}</dd>
          <dt className="text-muted-foreground">Tracking Number</dt>
          <dd className="font-mono break-all">{trackingNumber}</dd>
        </dl>
        <p className="text-xs text-muted-foreground">
          Sandbox tracker: the number is made up. PayPal&apos;s sandbox does not
          check it, and no parcel is sent.
        </p>
        {ship.isError && (
          <p className="text-sm text-destructive" role="alert">
            {errorDetail(ship.error)}
          </p>
        )}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button
            disabled={ship.isPending || ship.isSuccess}
            onClick={() => {
              ship.mutate({
                path: { number },
                body: { carrier: CARRIER, tracking_number: trackingNumber },
              });
            }}
          >
            {ship.isPending ? (
              <>
                <LoaderCircle
                  aria-hidden
                  className="motion-safe:animate-spin"
                />
                <span role="status">Sending to PayPal…</span>
              </>
            ) : (
              "Mark as Shipped"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
