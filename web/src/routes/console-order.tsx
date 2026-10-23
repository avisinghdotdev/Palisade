import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { Link } from "react-router";

import type { Order } from "@/api/client";

import { orderOptions } from "@/api/client/@tanstack/react-query.gen";
import { Channel } from "@/components/channel";
import { CopyButton } from "@/components/copy-button";
import { OrderLines } from "@/components/order-lines";
import { OrderStatus } from "@/components/order-status";
import { Button } from "@/components/ui/button";
import { formatAmount, formatUtc } from "@/money";
import { EVENT_LABELS } from "@/order-labels";
import { pageTitle } from "@/page-title";
import { errorDetail } from "@/shopper";

import type { Route } from "./+types/console-order";

export const meta: Route.MetaFunction = ({ params }) => [
  { title: pageTitle(`Order ${params.number}`, "Console") },
];

const SOURCES: Record<Order["events"][number]["source"], string> = {
  api: "Checkout",
  webhook: "PayPal Webhook",
};

/** A PayPal identifier with its name above it and a copy button beside it. */
function PayPalId({
  label,
  value,
  note,
  mono = true,
}: {
  label: string;
  value: null | string;
  note?: string;
  mono?: boolean;
}) {
  return (
    <div className="py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 flex min-h-6 items-center gap-1">
        {value === null ? (
          <span className="text-sm text-muted-foreground">None</span>
        ) : (
          <>
            <span className={`text-sm break-all ${mono ? "font-mono" : ""}`}>
              {value}
            </span>
            <CopyButton label={label} value={value} />
          </>
        )}
      </dd>
      {note !== undefined && (
        <dd className="text-xs text-muted-foreground">{note}</dd>
      )}
    </div>
  );
}

function Timeline({ events }: { events: Order["events"] }) {
  return (
    <ol className="relative ml-1.5 border-l border-border">
      {events.map((event) => (
        <li
          className="relative pb-6 pl-6 last:pb-0"
          key={`${event.at}/${event.to_status}/${event.webhook_event_id ?? event.source}`}
        >
          <span
            aria-hidden
            className="absolute top-1.5 -left-[5px] size-2.5 rounded-full border-2 border-card bg-primary ring-1 ring-primary/30"
          />
          <p className="flex flex-wrap items-baseline gap-x-3">
            <span className="font-medium">{EVENT_LABELS[event.to_status]}</span>
            <time
              className="text-sm text-muted-foreground tabular-nums"
              dateTime={event.at}
            >
              {formatUtc(event.at)}
            </time>
          </p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {SOURCES[event.source]}
            {event.detail !== null && (
              <span className="font-mono text-xs"> {event.detail}</span>
            )}
          </p>
        </li>
      ))}
    </ol>
  );
}

function Calls({ calls }: { calls: Order["calls"] }) {
  return (
    <div className="overflow-x-auto rounded-xl border bg-card">
      <table className="w-full min-w-[44rem] text-sm">
        <thead className="bg-secondary text-left text-muted-foreground">
          <tr>
            <th className="px-4 py-2.5 font-medium" scope="col">
              Call
            </th>
            <th className="px-4 py-2.5 text-right font-medium" scope="col">
              Response
            </th>
            <th className="px-4 py-2.5 font-medium" scope="col">
              PayPal Debug ID
            </th>
            <th className="px-4 py-2.5 font-medium" scope="col">
              Request ID
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {calls.map((call) => (
            <tr key={call.request_id}>
              <th
                className="px-4 py-2 text-left font-medium capitalize"
                scope="row"
              >
                {call.action}
              </th>
              <td className="px-4 py-2 text-right tabular-nums">
                {call.status_code ?? "No Response"}
              </td>
              <td className="px-4 py-2">
                {call.debug_id === null ? (
                  <span className="text-muted-foreground">None</span>
                ) : (
                  <span className="flex items-center gap-1">
                    <span className="font-mono text-xs">{call.debug_id}</span>
                    <CopyButton label="PayPal debug ID" value={call.debug_id} />
                  </span>
                )}
              </td>
              <td className="px-4 py-2">
                <span className="flex items-center gap-1">
                  <span className="font-mono text-xs">{call.request_id}</span>
                  <CopyButton
                    label="PayPal request ID"
                    value={call.request_id}
                  />
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ConsoleOrder({ params }: Route.ComponentProps) {
  // A missing order stays missing; retrying only delays the answer.
  const order = useQuery({
    ...orderOptions({ path: { number: params.number } }),
    retry: false,
  });

  return (
    <>
      <nav aria-label="Breadcrumb">
        <ol className="flex items-center gap-1 text-sm text-muted-foreground">
          <li>
            <Link className="hover:text-foreground" to="/merchant">
              Orders
            </Link>
          </li>
          <li aria-hidden>
            <ChevronRight className="size-3.5" />
          </li>
          <li aria-current="page" className="text-foreground">
            {params.number}
          </li>
        </ol>
      </nav>
      {order.isPending && (
        <p className="mt-6 text-sm text-muted-foreground" role="status">
          Loading the order…
        </p>
      )}
      {order.isError && (
        <div className="mt-6">
          <h1 className="text-xl font-semibold">
            Order {params.number} is Not Available
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {errorDetail(order.error)}
          </p>
          <div className="mt-6 flex gap-3">
            <Button
              onClick={() => {
                void order.refetch();
              }}
              variant="outline"
            >
              Try Again
            </Button>
            <Button asChild>
              <Link to="/merchant">See All Orders</Link>
            </Button>
          </div>
        </div>
      )}
      {order.data && (
        <>
          <header className="mt-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <div className="flex items-center gap-1">
                <h1 className="text-2xl font-semibold tracking-tight">
                  Order {order.data.number}
                </h1>
                <CopyButton label="order number" value={order.data.number} />
              </div>
              <OrderStatus status={order.data.status} />
              <span className="inline-flex h-5 items-center rounded-4xl border bg-card px-2 text-xs font-medium">
                <Channel channel={order.data.channel} />
              </span>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              {formatAmount(order.data.total.value)}{" "}
              {order.data.total.currency_code}, placed{" "}
              {formatUtc(order.data.created_at)} by {order.data.wallet_email}
              {order.data.wallet_demo && " (the demo buyer)"}.
            </p>
          </header>
          <div className="mt-8 grid items-start gap-10 lg:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="min-w-0 space-y-10">
              <section aria-labelledby="items">
                <h2 className="text-base font-semibold" id="items">
                  Items
                </h2>
                <div className="mt-3 overflow-x-auto rounded-xl border bg-card px-4">
                  <div className="min-w-[20rem] py-2">
                    <OrderLines
                      lines={order.data.lines}
                      total={order.data.total}
                    />
                  </div>
                </div>
              </section>
              <section aria-labelledby="timeline">
                <h2 className="text-base font-semibold" id="timeline">
                  Timeline
                </h2>
                <div className="mt-4">
                  <Timeline events={order.data.events} />
                </div>
              </section>
            </div>
            <aside
              aria-labelledby="paypal"
              className="rounded-xl border bg-card px-5 py-4 lg:sticky lg:top-6"
            >
              <h2 className="py-1" id="paypal">
                <img alt="PayPal" className="h-6" src="/paypal-logo-blue.svg" />
              </h2>
              <dl className="mt-1 divide-y">
                <PayPalId
                  label="PayPal Order ID"
                  value={order.data.paypal_order_id}
                />
                <PayPalId
                  label="Authorization ID"
                  note={
                    order.data.status === "authorized" &&
                    order.data.authorization_expires_at !== null
                      ? `PayPal holds the amount until ${formatUtc(order.data.authorization_expires_at)}; capture before then.`
                      : undefined
                  }
                  value={order.data.authorization_id}
                />
                <PayPalId label="Capture ID" value={order.data.capture_id} />
                <PayPalId
                  label="Payer Email"
                  mono={false}
                  value={order.data.payer_email}
                />
                {order.data.channel === "agent" && (
                  <PayPalId
                    label="Custom ID"
                    note="The signed mandate's ID and hash."
                    value={order.data.custom_id}
                  />
                )}
              </dl>
            </aside>
          </div>
          <section aria-labelledby="calls" className="mt-10">
            <h2 className="text-base font-semibold" id="calls">
              Calls to PayPal
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Quote the debug ID to PayPal support about any of these.
            </p>
            <div className="mt-3">
              <Calls calls={order.data.calls} />
            </div>
          </section>
        </>
      )}
    </>
  );
}
