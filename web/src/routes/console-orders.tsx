import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { ordersOptions } from "@/api/client/@tanstack/react-query.gen";
import { OrdersGrid } from "@/components/orders-grid";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/page-title";

import type { Route } from "./+types/console-orders";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Orders", "Console") },
];

export default function ConsoleOrders() {
  const orders = useQuery(ordersOptions());

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Orders</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every order placed at the store, with its PayPal payment.
          </p>
        </div>
        <Button
          disabled={orders.isFetching}
          onClick={() => {
            void orders.refetch();
          }}
          size="sm"
          variant="outline"
        >
          <RefreshCw
            aria-hidden
            className={orders.isFetching ? "animate-spin" : undefined}
          />
          Refresh
        </Button>
      </div>
      {orders.isError && (
        <p className="mt-4 text-sm text-destructive">
          The orders did not load. Refresh to try again.
        </p>
      )}
      <div className="mt-6">
        <OrdersGrid
          failed={orders.isError}
          loading={orders.isPending}
          orders={orders.data ?? []}
        />
      </div>
    </>
  );
}
