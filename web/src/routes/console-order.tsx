import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Truck } from "lucide-react";
import { Link } from "react-router";

import type { Order } from "@/api/client";

import { orderOptions } from "@/api/client/@tanstack/react-query.gen";
import { Channel } from "@/components/channel";
import { CopyButton } from "@/components/copy-button";
import { OrderLines } from "@/components/order-lines";
import { OrderStatus } from "@/components/order-status";
import { ShipOrderDialog } from "@/components/ship-order-dialog";
import { Button } from "@/components/ui/button";
import { formatAmount, formatUtc } from "@/money";
import { EVENT_LABELS, WEBHOOK_LABELS } from "@/order-labels";
import { pageTitle } from "@/page-title";
import { errorDetail } from "@/shopper";

import type { Route } from "./+types/console-order";

export const meta: Route.MetaFunction = ({ params }) => [
  { title: pageTitle(`Order ${params.number}`, "Console") },
];

const SOURCES: Record<Order["events"][number]["source"], string> = {
  api: "Checkout",
  webhook: "PayPal Webhook",
  repair: "Repair Job",
};

interface Entry {
  key: string;
  at: string;
  label: string;
  source: string;
  detail: null | string;
}

/** The order's events and its shipment, in the order they happened. */
function timeline(order: Order): Entry[] {
  const entries: Entry[] = order.events.map((event) => ({
    key: `${event.at}/${event.to_status}/${event.webhook_event_id ?? event.source}`,
    at: event.at,
    label:
      event.paypal_event === null
        ? EVENT_LABELS[event.to_status]
        : WEBHOOK_LABELS[event.paypal_event],
    source: SOURCES[event.source],
    detail: event.detail,
  }));
  if (order.shipment !== null) {
    entries.push({
      key: order.shipment.paypal_tracker_id,
      at: order.shipment.shipped_at,
      label: "Marked as Shipped",
      source: "Merchant Console",
      detail: `${order.shipment.carrier} ${order.shipment.tracking_number}`,
    });
  }
  // Stable, so events at the same instant keep the order they were recorded in.
  return entries.toSorted((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

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

function Timeline({ order }: { order: Order }) {
  return (
    <ol className="relative ml-1.5 border-l border-border">
      {timeline(order).map((entry) => (
        <li className="relative pb-6 pl-6 last:pb-0" key={entry.key}>
          <span
            aria-hidden
            className="absolute top-1.5 -left-[5px] size-2.5 rounded-full border-2 border-card bg-primary ring-1 ring-primary/30"
          />
          <p className="flex flex-wrap items-baseline gap-x-3">
            <span className="font-medium">{entry.label}</span>
            <time
              className="text-sm text-muted-foreground tabular-nums"
              dateTime={entry.at}
            >
              {formatUtc(entry.at)}
            </time>
          </p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {entry.source}
            {entry.detail !== null && (
              <span className="font-mono text-xs"> {entry.detail}</span>
            )}
          </p>
        </li>
      ))}
    </ol>
  );
}

/** The order's tracking once shipped; until then, the way to ship a paid order. */
function Shipping({ order }: { order: Order }) {
  return (
    <section
      aria-labelledby="shipping"
      className="rounded-xl border bg-card px-5 py-4"
    >
      <h2
        className="flex items-center gap-2 py-1 text-base font-semibold"
        id="shipping"
      >
        <Truck aria-hidden className="size-4 text-muted-foreground" />
        Shipping
      </h2>
      {order.shipment === null ? (
        <>
          <p className="mt-2 text-sm text-muted-foreground">
            {order.status === "captured"
              ? "Paid and not shipped yet."
              : "An order ships once its payment is captured."}
          </p>
          {order.status === "captured" && (
            <ShipOrderDialog number={order.number} />
          )}
        </>
      ) : (
        <>
          <dl className="mt-1 divide-y">
            <PayPalId
              label="Carrier"
              mono={false}
              value={order.shipment.carrier}
            />
            <PayPalId
              label="Tracking Number"
              note={`Shipped ${formatUtc(order.shipment.shipped_at)}.`}
              value={order.shipment.tracking_number}
            />
          </dl>
          <p className="text-xs text-muted-foreground">
            On the PayPal capture, so a dispute about delivery has proof to
            file.
          </p>
        </>
      )}
    </section>
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
            <Link className="hover:text-foreground" to="/console">
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
              <Link to="/console">See All Orders</Link>
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
              {order.data.wallet_demo && " (the demo buyer)"}
              {order.data.agent !== null && ` through ${order.data.agent.name}`}
              .
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
                  <Timeline order={order.data} />
                </div>
              </section>
            </div>
            <div className="space-y-6 lg:sticky lg:top-6">
              {order.data.agent !== null && (
                <aside
                  aria-labelledby="agent"
                  className="rounded-xl border bg-card px-5 py-4"
                >
                  <h2 className="py-1 text-base font-semibold" id="agent">
                    Agent
                  </h2>
                  <dl className="mt-1 divide-y">
                    <PayPalId
                      label="Agent"
                      mono={false}
                      note={`Signed every request as ${order.data.agent.id}.`}
                      value={order.data.agent.name}
                    />
                    <PayPalId
                      label="Policy Hash"
                      note="The merchant's rules this order passed, as the mandate records them."
                      value={order.data.policy_hash}
                    />
                  </dl>
                </aside>
              )}
              <aside
                aria-labelledby="paypal"
                className="rounded-xl border bg-card px-5 py-4"
              >
                <h2 className="py-1" id="paypal">
                  <img
                    alt="PayPal"
                    className="h-6"
                    src="/paypal-logo-blue.svg"
                  />
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
              <Shipping order={order.data} />
            </div>
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
