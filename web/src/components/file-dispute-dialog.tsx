import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Gavel, LoaderCircle } from "lucide-react";
import { useNavigate } from "react-router";

import type { SimulateDisputeData } from "@/api/client";

import {
  disputeQueryKey,
  disputesQueryKey,
  orderActivityQueryKey,
  simulateDisputeMutation,
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
import { REASON_LABELS } from "@/dispute-labels";
import { errorDetail } from "@/shopper";

type Reason = SimulateDisputeData["body"]["reason"];

const REASONS: { reason: Reason; note: string }[] = [
  {
    reason: "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
    note: "The buyer says the shoes are not what they asked for. PayPal opens an inquiry.",
  },
  {
    reason: "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    note: "The buyer says nothing arrived. PayPal opens an inquiry; tracking answers it.",
  },
  {
    reason: "OTHER",
    note: "PayPal opens it straight as a claim it decides itself.",
  },
];

/** The demo buyer disputes a paid order through its own PayPal app, as a buyer would from PayPal. */
export function FileDisputeDialog({ number }: { number: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const file = useMutation({
    ...simulateDisputeMutation(),
    onSuccess: (dispute) => {
      queryClient.setQueryData(
        disputeQueryKey({ path: { dispute_id: dispute.id } }),
        dispute,
      );
      void queryClient.invalidateQueries({ queryKey: disputesQueryKey() });
      void queryClient.invalidateQueries({
        queryKey: orderActivityQueryKey({ path: { number } }),
      });
      void navigate(`/console/disputes/${dispute.id}`);
    },
  });

  return (
    <Dialog
      onOpenChange={(open) => {
        if (open) file.reset();
      }}
    >
      <DialogTrigger asChild>
        <Button className="mt-3 w-full" size="sm" variant="outline">
          <Gavel aria-hidden />
          File a Dispute (Sandbox Simulator)
        </Button>
      </DialogTrigger>
      {/* The dialog renders outside the page, so it takes the shop's theme itself. */}
      <DialogContent className="theme-shop">
        <DialogHeader>
          <DialogTitle>File a Dispute on Order {number}</DialogTitle>
          <DialogDescription>
            Sandbox simulator: the demo buyer files through its own PayPal app,
            as a buyer would from their PayPal account. PayPal can dispute a
            payment once its transaction search lists it, up to three hours
            after the payment.
          </DialogDescription>
        </DialogHeader>
        <ul className="grid gap-2">
          {REASONS.map(({ reason, note }) => (
            <li key={reason}>
              <Button
                className="h-auto w-full flex-col items-start gap-0.5 py-2.5 text-left whitespace-normal"
                disabled={file.isPending}
                onClick={() => {
                  file.mutate({ path: { number }, body: { reason } });
                }}
                variant="outline"
              >
                <span className="font-medium">{REASON_LABELS[reason]}</span>
                <span className="text-xs font-normal text-muted-foreground">
                  {note}
                </span>
              </Button>
            </li>
          ))}
        </ul>
        {file.isPending && (
          <p
            className="flex items-center gap-2 text-sm text-muted-foreground"
            role="status"
          >
            <LoaderCircle
              aria-hidden
              className="size-4 motion-safe:animate-spin"
            />
            Filing with PayPal as the demo buyer…
          </p>
        )}
        {file.isError && (
          <p className="text-sm text-destructive" role="alert">
            {errorDetail(file.error)}
          </p>
        )}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
