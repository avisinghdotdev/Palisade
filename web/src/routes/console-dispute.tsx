import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  FileText,
  FlaskConical,
  LoaderCircle,
  MessageSquare,
  RefreshCw,
  ShoppingBag,
} from "lucide-react";
import { Link } from "react-router";

import type { Dispute } from "@/api/client";

import { activityEntry } from "@/activity-labels";
import {
  disputeActivityOptions,
  disputeActivityQueryKey,
  disputeOptions,
  disputeQueryKey,
  disputesQueryKey,
  simulateAdjudicationMutation,
  syncDisputeMutation,
} from "@/api/client/@tanstack/react-query.gen";
import { Channel } from "@/components/channel";
import { CopyButton } from "@/components/copy-button";
import { DetailField } from "@/components/detail-field";
import { DisputeStatus } from "@/components/dispute-status";
import { Timeline } from "@/components/timeline";
import { Button } from "@/components/ui/button";
import {
  actionLabel,
  CHANNEL_LABELS,
  REASON_LABELS,
  stageLabel,
} from "@/dispute-labels";
import { formatAmount, formatUtc } from "@/money";
import { pageTitle } from "@/page-title";
import { errorDetail } from "@/shopper";

import type { Route } from "./+types/console-dispute";

export const meta: Route.MetaFunction = ({ params }) => [
  { title: pageTitle(`Dispute ${params.disputeId}`, "Console") },
];

// PayPal's webhooks move an open dispute along; the page reads the store's copy again this often.
const OPEN_DISPUTE_REFRESH_MS = 15_000;

const SOURCES: Record<string, string> = {
  SUBMITTED_BY_BUYER: "Filed by the buyer",
  SUBMITTED_BY_SELLER: "Filed by the seller",
  SUBMITTED_BY_PARTNER: "Filed by a PayPal partner",
  REQUESTED_FROM_SELLER: "PayPal asks the seller for this",
  REQUESTED_FROM_BUYER: "PayPal asks the buyer for this",
};

/** Keep every view of the dispute in step with the copy the API just returned. */
function useStoreDispute() {
  const queryClient = useQueryClient();
  return (dispute: Dispute) => {
    const path = { dispute_id: dispute.id };
    queryClient.setQueryData(disputeQueryKey({ path }), dispute);
    void queryClient.invalidateQueries({
      queryKey: disputeActivityQueryKey({ path }),
    });
    void queryClient.invalidateQueries({ queryKey: disputesQueryKey() });
  };
}

function Messages({ dispute }: { dispute: Dispute }) {
  return (
    <section aria-labelledby="messages">
      <h2
        className="flex items-center gap-2 text-base font-semibold"
        id="messages"
      >
        <MessageSquare aria-hidden className="size-4 text-muted-foreground" />
        Messages
      </h2>
      {dispute.messages.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          Nobody has written on this dispute.
        </p>
      ) : (
        <ul className="mt-3 grid gap-3">
          {dispute.messages.map((message) => (
            <li
              className="rounded-xl border bg-card px-4 py-3"
              key={`${message.at}/${message.posted_by}`}
            >
              <p className="text-xs text-muted-foreground">
                {message.posted_by === "BUYER" ? "The buyer" : "The seller"},{" "}
                {formatUtc(message.at)}
              </p>
              <p className="mt-1 text-sm whitespace-pre-line">
                {message.content}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Evidence({ dispute }: { dispute: Dispute }) {
  return (
    <section aria-labelledby="evidence">
      <h2
        className="flex items-center gap-2 text-base font-semibold"
        id="evidence"
      >
        <FileText aria-hidden className="size-4 text-muted-foreground" />
        Evidence on File with PayPal
      </h2>
      {dispute.evidences.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          PayPal lists no evidence yet.
        </p>
      ) : (
        <ul className="mt-3 grid gap-3">
          {dispute.evidences.map((evidence) => (
            <li
              className="rounded-xl border bg-card px-4 py-3"
              key={`${evidence.at}/${evidence.evidence_type}`}
            >
              <p className="flex flex-wrap items-baseline gap-x-3">
                <span className="font-mono text-xs font-medium">
                  {evidence.evidence_type}
                </span>
                <span className="text-xs text-muted-foreground">
                  {SOURCES[evidence.source] ?? evidence.source}
                  {evidence.stage !== null &&
                    ` in the ${stageLabel(evidence.stage, dispute.dispute_channel).toLowerCase()} stage`}
                  , {formatUtc(evidence.at)}
                </span>
              </p>
              {evidence.notes !== null && (
                <p className="mt-1 text-sm whitespace-pre-line">
                  {evidence.notes}
                </p>
              )}
              {evidence.documents.length > 0 && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Attached: {evidence.documents.join(", ")}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function OrderPanel({ dispute }: { dispute: Dispute }) {
  return (
    <aside
      aria-labelledby="order"
      className="rounded-xl border bg-card px-5 py-4"
    >
      <h2
        className="flex items-center gap-2 py-1 text-base font-semibold"
        id="order"
      >
        <ShoppingBag aria-hidden className="size-4 text-muted-foreground" />
        Order
      </h2>
      <dl className="mt-1 divide-y">
        <div className="py-3">
          <dt className="text-xs text-muted-foreground">Order</dt>
          <dd className="mt-0.5 flex items-center gap-3 text-sm">
            <Link
              className="font-medium text-primary underline-offset-2 hover:underline"
              to={`/console/orders/${dispute.order_number}`}
            >
              {dispute.order_number}
            </Link>
            <Channel channel={dispute.order_channel} />
          </dd>
        </div>
        {dispute.mandate_id !== null && (
          <DetailField
            label="Mandate ID"
            note={
              dispute.custom_matches === true
                ? "PayPal holds this mandate's ID and hash on the disputed payment."
                : "PayPal's custom ID on the payment does not match this mandate."
            }
            value={dispute.mandate_id}
          />
        )}
      </dl>
    </aside>
  );
}

function PayPalPanel({ dispute }: { dispute: Dispute }) {
  return (
    <aside
      aria-labelledby="paypal"
      className="rounded-xl border bg-card px-5 py-4"
    >
      <h2 className="py-1" id="paypal">
        <img alt="PayPal" className="h-6" src="/paypal-logo-blue.svg" />
      </h2>
      <dl className="mt-1 divide-y">
        <DetailField
          label="Channel"
          mono={false}
          value={
            dispute.dispute_channel === null
              ? null
              : CHANNEL_LABELS[dispute.dispute_channel]
          }
        />
        <DetailField label="Capture ID" value={dispute.capture_id} />
        <DetailField
          label="Buyer's Transaction ID"
          note="The buyer sees the same payment under its own ID."
          value={dispute.buyer_transaction_id}
        />
        <DetailField
          label="Seller Protection"
          mono={false}
          value={
            dispute.seller_protection_eligible === null
              ? null
              : dispute.seller_protection_eligible
                ? "Eligible"
                : "Not eligible"
          }
        />
        <div className="py-3">
          <dt className="text-xs text-muted-foreground">PayPal Offers Now</dt>
          <dd className="mt-1 text-sm">
            {dispute.actions.length === 0
              ? "No action; PayPal is deciding or has decided."
              : dispute.actions.map(actionLabel).join(", ")}
          </dd>
        </div>
      </dl>
      <p className="text-xs text-muted-foreground">
        Read from PayPal {formatUtc(dispute.synced_at)}.
      </p>
    </aside>
  );
}

function simulatorNote(
  dispute: Dispute,
  offered: boolean,
  requested: boolean,
): string {
  if (dispute.status === "RESOLVED") return "PayPal has decided this dispute.";
  if (requested)
    return "Decision requested. PayPal resolves the claim within a few minutes, and the webhook brings the outcome here.";
  if (offered)
    return "PayPal's sandbox can decide this claim now, as its reviewer would.";
  return "PayPal's sandbox offers a decision once a claim has been under review a while, often hours.";
}

/** The sandbox's stand-in for PayPal's reviewer, offered only once PayPal itself offers it. */
function Simulator({ dispute }: { dispute: Dispute }) {
  const store = useStoreDispute();
  const decide = useMutation({
    ...simulateAdjudicationMutation(),
    onSuccess: store,
  });
  const offered = dispute.actions.includes("adjudicate");

  return (
    <aside
      aria-labelledby="simulator"
      className="rounded-xl border border-dashed bg-card px-5 py-4"
    >
      <h2
        className="flex items-center gap-2 py-1 text-base font-semibold"
        id="simulator"
      >
        <FlaskConical aria-hidden className="size-4 text-muted-foreground" />
        Sandbox Simulator
      </h2>
      <p className="mt-1 text-sm text-muted-foreground" role="status">
        {simulatorNote(dispute, offered, decide.isSuccess)}
      </p>
      {offered && (
        <div className="mt-3 grid gap-2">
          {(["SELLER_FAVOR", "BUYER_FAVOR"] as const).map((outcome) => (
            <Button
              disabled={decide.isPending}
              key={outcome}
              onClick={() => {
                decide.mutate({
                  path: { dispute_id: dispute.id },
                  body: { outcome },
                });
              }}
              size="sm"
              variant="outline"
            >
              {outcome === "SELLER_FAVOR"
                ? "Decide for the Seller"
                : "Decide for the Buyer"}
            </Button>
          ))}
        </div>
      )}
      {decide.isPending && (
        <p
          className="mt-2 flex items-center gap-2 text-sm text-muted-foreground"
          role="status"
        >
          <LoaderCircle
            aria-hidden
            className="size-4 motion-safe:animate-spin"
          />
          Asking PayPal…
        </p>
      )}
      {decide.isError && (
        <p className="mt-2 text-sm text-destructive" role="alert">
          {errorDetail(decide.error)}
        </p>
      )}
    </aside>
  );
}

export default function ConsoleDispute({ params }: Route.ComponentProps) {
  const path = { dispute_id: params.disputeId };
  // A missing dispute stays missing; retrying only delays the answer.
  const dispute = useQuery({
    ...disputeOptions({ path }),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === "RESOLVED" ? false : OPEN_DISPUTE_REFRESH_MS,
  });
  // Asked for alongside the dispute: an unknown dispute has no rows, so nothing waits on the other.
  const activity = useQuery({
    ...disputeActivityOptions({ path }),
    refetchInterval:
      dispute.data?.status === "RESOLVED" ? false : OPEN_DISPUTE_REFRESH_MS,
  });
  const store = useStoreDispute();
  const sync = useMutation({ ...syncDisputeMutation(), onSuccess: store });

  return (
    <>
      <nav aria-label="Breadcrumb">
        <ol className="flex items-center gap-1 text-sm text-muted-foreground">
          <li>
            <Link className="hover:text-foreground" to="/console/disputes">
              Disputes
            </Link>
          </li>
          <li aria-hidden>
            <ChevronRight className="size-3.5" />
          </li>
          <li aria-current="page" className="text-foreground">
            {params.disputeId}
          </li>
        </ol>
      </nav>
      {dispute.isPending && (
        <p className="mt-6 text-sm text-muted-foreground" role="status">
          Loading the dispute…
        </p>
      )}
      {dispute.isError && (
        <div className="mt-6">
          <h1 className="text-xl font-semibold">
            Dispute {params.disputeId} is not Available
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {errorDetail(dispute.error)}
          </p>
          <div className="mt-6 flex gap-3">
            <Button
              onClick={() => {
                void dispute.refetch();
              }}
              variant="outline"
            >
              Try Again
            </Button>
            <Button asChild>
              <Link to="/console/disputes">See All Disputes</Link>
            </Button>
          </div>
        </div>
      )}
      {dispute.data && (
        <>
          <header className="mt-3 flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <div className="flex items-center gap-1">
                  <h1 className="text-2xl font-semibold tracking-tight">
                    Dispute {dispute.data.id}
                  </h1>
                  <CopyButton label="dispute ID" value={dispute.data.id} />
                </div>
                <DisputeStatus
                  outcome={dispute.data.outcome}
                  status={dispute.data.status}
                />
                <span className="inline-flex h-5 items-center rounded-4xl border bg-card px-2 text-xs font-medium">
                  {stageLabel(dispute.data.stage, dispute.data.dispute_channel)}
                </span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {REASON_LABELS[dispute.data.reason]},{" "}
                {formatAmount(dispute.data.amount.value)}{" "}
                {dispute.data.amount.currency_code}, opened{" "}
                {formatUtc(dispute.data.opened_at)}.
                {dispute.data.status === "WAITING_FOR_SELLER_RESPONSE" &&
                  dispute.data.seller_response_due_at !== null &&
                  ` The seller must respond by ${formatUtc(dispute.data.seller_response_due_at)}.`}
              </p>
            </div>
            <Button
              disabled={sync.isPending}
              onClick={() => {
                sync.mutate({ path });
              }}
              size="sm"
              variant="outline"
            >
              <RefreshCw
                aria-hidden
                className={sync.isPending ? "animate-spin" : undefined}
              />
              Read from PayPal
            </Button>
          </header>
          {sync.isError && (
            <p className="mt-3 text-sm text-destructive" role="alert">
              {errorDetail(sync.error)}
            </p>
          )}
          <div className="mt-8 grid items-start gap-10 lg:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="min-w-0 space-y-10">
              <Messages dispute={dispute.data} />
              <Evidence dispute={dispute.data} />
              <section aria-labelledby="timeline">
                <h2 className="text-base font-semibold" id="timeline">
                  Timeline
                </h2>
                <div className="mt-4">
                  {activity.isPending && (
                    <p className="text-sm text-muted-foreground" role="status">
                      Loading the timeline…
                    </p>
                  )}
                  {activity.isError && (
                    <p className="text-sm text-destructive">
                      The timeline did not load.
                    </p>
                  )}
                  {activity.data !== undefined && (
                    <Timeline entries={activity.data.map(activityEntry)} />
                  )}
                </div>
              </section>
            </div>
            <div className="space-y-6 lg:sticky lg:top-6">
              <OrderPanel dispute={dispute.data} />
              <PayPalPanel dispute={dispute.data} />
              <Simulator dispute={dispute.data} />
            </div>
          </div>
        </>
      )}
    </>
  );
}
