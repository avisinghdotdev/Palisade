import { LoaderCircle } from "lucide-react";

import type { TriageDecision } from "@/api/client";

import { Badge } from "@/components/ui/badge";
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
import { TRIAGE_ACTION_LABELS } from "@/dispute-labels";
import { formatAmount, formatUtc } from "@/money";

const STATUS_LABELS: Record<TriageDecision["status"], string> = {
  proposed: "Waiting for the Merchant",
  approved: "Approved, Waiting for PayPal",
  done: "Done",
  refused: "Refused by PayPal",
};

type Sent = Exclude<TriageDecision["action"], "fight">;

const BUTTONS: Record<Sent, string> = {
  message: "Send the Message",
  offer: "Make the Offer",
  accept: "Refund in Full",
};

/** The question the merchant answers before an answer that moves money goes to PayPal. */
const GUARDS: Record<
  Exclude<Sent, "message">,
  { title: string; note: string }
> = {
  offer: {
    title: "Offer the Buyer a Partial Refund?",
    note: "PayPal refunds this amount and closes the dispute as soon as the buyer accepts. An accepted offer cannot be undone.",
  },
  accept: {
    title: "Refund the Buyer in Full?",
    note: "PayPal returns the whole payment to the buyer and closes the dispute. A refund cannot be undone.",
  },
};

function ConfirmButton({
  action,
  busy,
  decision,
  onConfirm,
}: {
  action: Sent;
  busy: boolean;
  decision: TriageDecision;
  onConfirm: () => void;
}) {
  if (action === "message") {
    return (
      <Button className="mt-3" disabled={busy} onClick={onConfirm} size="sm">
        {BUTTONS.message}
      </Button>
    );
  }
  const guard = GUARDS[action];
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button className="mt-3" disabled={busy} size="sm">
          {BUTTONS[action]}
        </Button>
      </DialogTrigger>
      {/* The dialog renders outside the page, so it takes the shop's theme itself. */}
      <DialogContent className="theme-shop">
        <DialogHeader>
          <DialogTitle>{guard.title}</DialogTitle>
          <DialogDescription>
            {decision.offer !== null &&
              `${formatAmount(decision.offer.value)}. `}
            {guard.note}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Keep the Payment</Button>
          </DialogClose>
          <DialogClose asChild>
            <Button onClick={onConfirm}>{BUTTONS[action]}</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** One round of the inquiry's triage: what Claude read, what it picked and why, and the merchant's go. */
export function TriageCard({
  busy,
  decision,
  onConfirm,
}: {
  busy: boolean;
  decision: TriageDecision;
  onConfirm: () => void;
}) {
  const open = decision.status === "proposed" || decision.status === "approved";

  return (
    <li className="rounded-xl border bg-card px-4 py-3">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-medium">
          Claude Picked: {TRIAGE_ACTION_LABELS[decision.action]}
          {decision.offer !== null &&
            ` of ${formatAmount(decision.offer.value)}`}
        </span>
        <Badge
          className={
            decision.status === "refused"
              ? "border-destructive/40 text-destructive"
              : decision.status === "done"
                ? "border-emerald-600/40 bg-emerald-50 text-emerald-800"
                : ""
          }
          variant="outline"
        >
          {STATUS_LABELS[decision.status]}
        </Badge>
        <span className="text-xs text-muted-foreground">
          Picked {formatUtc(decision.decided_at)}
          {decision.done_at !== null &&
            decision.action !== "fight" &&
            `, sent ${formatUtc(decision.done_at)}`}
        </span>
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Allowed by PayPal and the merchant&apos;s rules:{" "}
        {decision.options
          .map((option) => TRIAGE_ACTION_LABELS[option])
          .join(", ")}
        . Claude picks; the rules are checked in code.
      </p>
      <dl className="mt-2 grid gap-2 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">The Complaint</dt>
          <dd>{decision.complaint}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Why</dt>
          <dd>
            <ul className="list-disc pl-5">
              {decision.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </dd>
        </div>
        {decision.message !== null && (
          <div>
            <dt className="text-xs text-muted-foreground">
              What the Buyer Reads
            </dt>
            <dd className="whitespace-pre-line">{decision.message}</dd>
          </div>
        )}
      </dl>
      {open && decision.action !== "fight" && (
        <ConfirmButton
          action={decision.action}
          busy={busy}
          decision={decision}
          onConfirm={onConfirm}
        />
      )}
      {open && busy && (
        <p
          className="mt-2 flex items-center gap-2 text-sm text-muted-foreground"
          role="status"
        >
          <LoaderCircle
            aria-hidden
            className="size-4 motion-safe:animate-spin"
          />
          Sending to PayPal…
        </p>
      )}
      {decision.request_id !== null && (
        <p className="mt-3 font-mono text-xs break-all text-muted-foreground">
          PayPal-Request-Id {decision.request_id}
          {decision.status_code !== null &&
            ` · HTTP ${String(decision.status_code)}`}
          {decision.debug_id !== null && ` · debug id ${decision.debug_id}`}
        </p>
      )}
    </li>
  );
}
