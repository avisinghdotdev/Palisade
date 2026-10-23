import type { ReactNode } from "react";

import type { Order } from "@/api/client";

import { CopyButton } from "@/components/copy-button";
import { PalisadeLogo } from "@/components/palisade-logo";
import { ShopLogo } from "@/components/shop-logo";
import { formatAmount, formatLocal } from "@/money";

const STAMPS: Record<Order["status"], { text: string; tone: string }> = {
  captured: { text: "Paid", tone: "border-emerald-700 text-emerald-700" },
  capture_pending: { text: "Pending", tone: "border-amber-700 text-amber-700" },
  authorized: { text: "Pending", tone: "border-amber-700 text-amber-700" },
  creating: { text: "Pending", tone: "border-amber-700 text-amber-700" },
  declined: { text: "Declined", tone: "border-red-700 text-red-700" },
  failed: { text: "Not Confirmed", tone: "border-red-700 text-red-700" },
  voided: { text: "Voided", tone: "border-stone-500 text-stone-500" },
  refunded: { text: "Refunded", tone: "border-stone-500 text-stone-500" },
  reversed: { text: "Reversed", tone: "border-red-700 text-red-700" },
};

function Rule() {
  return <hr className="my-4 border-t border-dashed border-stone-300" />;
}

/** One receipt line: the label, a dotted leader to the right edge, and the value. */
function Line({
  label,
  children,
  strong = false,
}: {
  label: string;
  children: ReactNode;
  strong?: boolean;
}) {
  return (
    <div
      className={`flex items-baseline ${strong ? "text-base font-semibold" : ""}`}
    >
      <dt className="flex flex-1 items-baseline after:mx-2 after:flex-1 after:translate-y-[-0.2em] after:border-b after:border-dotted after:border-stone-400 after:content-['']">
        {label}
      </dt>
      <dd className="flex items-center gap-1 tabular-nums">{children}</dd>
    </div>
  );
}

/** The order as a till receipt: the store's name, the lines, the total, and how it was paid. */
export function Receipt({ order }: { order: Order }) {
  const stamp = STAMPS[order.status];

  return (
    <div className="drop-shadow-[0_10px_18px_rgb(68_44_24/0.14)] print:drop-shadow-none">
      <article
        aria-label={`Receipt for order ${order.number}`}
        className="receipt-tear relative bg-white px-7 pt-8 pb-12 text-sm text-stone-800 print:border print:border-stone-300 print:pb-8 print:mask-none"
      >
        <header className="text-center">
          <ShopLogo className="mx-auto size-8 text-primary" />
          <p className="mt-2 text-base font-semibold tracking-tight">
            Ridgeline Running
          </p>
          <p className="text-xs text-stone-500">
            Demo store in the PayPal sandbox
          </p>
        </header>
        <Rule />
        <dl className="space-y-1.5">
          <Line label="Order">
            <span className="font-medium">{order.number}</span>
            <CopyButton label="order number" value={order.number} />
          </Line>
          <Line label="Date">{formatLocal(order.created_at)}</Line>
        </dl>
        <Rule />
        <ul aria-label="Items" className="space-y-3">
          {order.lines.map((line) => (
            <li key={`${line.product_id}/${line.size}`}>
              <p className="font-medium">
                {line.brand} {line.name}
              </p>
              <dl>
                <Line label={`US ${line.size} × ${String(line.quantity)}`}>
                  {formatAmount(line.line_total.value)}
                </Line>
              </dl>
            </li>
          ))}
        </ul>
        <Rule />
        <dl>
          <Line label={`Total ${order.total.currency_code}`} strong>
            {formatAmount(order.total.value)}
          </Line>
        </dl>
        <Rule />
        <dl>
          <Line label="Paid with">
            <img
              alt="PayPal"
              className="h-5 translate-y-1"
              src="/paypal-logo-blue.svg"
            />
          </Line>
        </dl>
        {order.payer_email !== null && (
          <p className="mt-1 text-right break-all text-stone-600">
            {order.payer_email}
          </p>
        )}
        {order.capture_id !== null && (
          <dl className="mt-4">
            <Line label="Transaction ID">
              <span className="font-mono text-xs">{order.capture_id}</span>
              <CopyButton
                label="PayPal transaction ID"
                value={order.capture_id}
              />
            </Line>
          </dl>
        )}
        <p
          className={`mx-auto mt-6 w-fit -rotate-6 rounded-md border-[3px] px-4 py-1 text-xl font-bold tracking-widest uppercase opacity-80 motion-safe:animate-in motion-safe:fade-in motion-safe:zoom-in-150 motion-safe:duration-500 ${stamp.tone}`}
        >
          {stamp.text}
        </p>
        <p className="mt-6 text-center text-xs text-stone-500">
          Test money only. Nothing ships.
        </p>
        {order.channel === "agent" && (
          <>
            <Rule />
            <p className="theme-palisade flex items-center justify-center gap-1.5 text-xs text-stone-500">
              Purchase protected by
              <span className="inline-flex items-center gap-1 font-semibold text-primary">
                <PalisadeLogo className="size-4" />
                Palisade
              </span>
            </p>
          </>
        )}
      </article>
    </div>
  );
}
