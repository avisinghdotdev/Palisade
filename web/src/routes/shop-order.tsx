import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
import { Link } from "react-router";

import type { Order } from "@/api/client";

import { orderOptions } from "@/api/client/@tanstack/react-query.gen";
import { ConsoleLogo } from "@/components/console-logo";
import { PalisadeBadge } from "@/components/palisade-badge";
import { Receipt } from "@/components/receipt";
import { ShopHeader } from "@/components/shop-header";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/page-title";
import { errorDetail } from "@/shopper";

import type { Route } from "./+types/shop-order";

export const meta: Route.MetaFunction = ({ params }) => [
  { title: pageTitle(`Receipt ${params.number}`, "Shop") },
];

// Statuses the checkout or its repair job moves on from within minutes; until then the page
// asks again by itself, so a shopper who reloaded early sees the outcome arrive.
const SETTLING = new Set<Order["status"]>(["creating", "authorized", "failed"]);
const SETTLING_REFETCH_MS = 5_000;

const HEADLINES: Record<Order["status"], { title: string; detail: string }> = {
  captured: {
    title: "Your Order is Placed",
    detail: "PayPal took the payment from your saved wallet.",
  },
  capture_pending: {
    title: "Your Order is Placed",
    detail: "PayPal is still settling the payment.",
  },
  authorized: {
    title: "Your Payment is Being Completed",
    detail: "PayPal holds the amount; the charge follows shortly.",
  },
  creating: {
    title: "Your Payment is Being Completed",
    detail: "Waiting for PayPal to answer.",
  },
  declined: {
    title: "Your Payment was Declined",
    detail: "Nothing was charged. Your cart is still there to try again.",
  },
  failed: {
    title: "We Could Not Confirm Your Payment",
    detail:
      "PayPal did not answer in time. The shop asks PayPal again within minutes, and this page follows.",
  },
  voided: {
    title: "Your Payment was Released",
    detail: "The hold on your wallet was cancelled; nothing was charged.",
  },
  refunded: {
    title: "This Order was Refunded",
    detail: "The full amount went back to your PayPal wallet.",
  },
  reversed: {
    title: "This Payment was Reversed",
    detail: "PayPal took the payment back from the store.",
  },
};

export default function ShopOrder({ params }: Route.ComponentProps) {
  // A missing order stays missing; retrying only delays the answer.
  const order = useQuery({
    ...orderOptions({ path: { number: params.number } }),
    retry: false,
    refetchInterval: (query) =>
      query.state.data !== undefined && SETTLING.has(query.state.data.status)
        ? SETTLING_REFETCH_MS
        : false,
  });

  return (
    <div className="theme-shop flex min-h-dvh flex-col bg-secondary text-foreground print:min-h-0 print:bg-white print:[page:receipt]">
      <div className="print:hidden">
        <ShopHeader to="/shop">
          <PalisadeBadge>Protected by Palisade</PalisadeBadge>
        </ShopHeader>
      </div>
      <main className="mx-auto w-full max-w-md flex-1 px-4 py-10 print:px-0 print:pt-[18mm] print:pb-0">
        {order.isPending && (
          <p
            className="text-center text-sm text-muted-foreground"
            role="status"
          >
            Loading your receipt…
          </p>
        )}
        {order.isError && (
          <div className="text-center">
            <h1 className="text-xl font-semibold">
              Receipt {params.number} is Not Available
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {errorDetail(order.error)}
            </p>
            <div className="mt-6 flex justify-center gap-3">
              <Button
                onClick={() => {
                  void order.refetch();
                }}
                variant="outline"
              >
                Try Again
              </Button>
              <Button asChild>
                <Link to="/shop">Back to the Shop</Link>
              </Button>
            </div>
          </div>
        )}
        {order.data && (
          <>
            <div className="text-center print:hidden">
              <h1 className="text-2xl font-semibold tracking-tight">
                {HEADLINES[order.data.status].title}
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {HEADLINES[order.data.status].detail}
              </p>
            </div>
            <div className="mt-8 print:mt-0">
              <Receipt order={order.data} />
            </div>
            <div className="mt-8 flex flex-col gap-3 print:hidden">
              <Button asChild>
                <Link to="/shop">Continue Shopping</Link>
              </Button>
              <div className="flex flex-col gap-3 sm:flex-row">
                <Button
                  className="sm:flex-1"
                  onClick={() => {
                    window.print();
                  }}
                  variant="outline"
                >
                  <Printer aria-hidden />
                  Print or Save as PDF
                </Button>
                <Button asChild className="sm:flex-1" variant="outline">
                  <Link to={`/console/orders/${order.data.number}`}>
                    <ConsoleLogo />
                    View in Merchant Console
                  </Link>
                </Button>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
