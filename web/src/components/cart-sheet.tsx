import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Minus, Plus, ShoppingBag, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

import type { Cart, Wallet } from "@/api/client";

import { checkout } from "@/api/client";
import {
  orderQueryKey,
  productsOptions,
} from "@/api/client/@tanstack/react-query.gen";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useCart } from "@/hooks/use-cart";
import { formatAmount } from "@/money";
import { errorDetail, useShopper } from "@/shopper";

/** The cart behind the bag button: lines to adjust, the total, and the PayPal payment. */
export function CartSheet({ wallet }: { wallet: undefined | Wallet }) {
  const { setCartId } = useShopper();
  const { cart, setLine } = useCart(wallet?.id);
  const products = useQuery(productsOptions());
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState<null | string>(null);

  const pay = useMutation({
    mutationFn: async (current: Cart) => {
      const placed = await checkout({
        path: { cart_id: current.id },
        throwOnError: true,
      });
      return placed.data;
    },
    onSuccess: (order) => {
      // The checkout answers with the whole order, so the receipt opens without asking again.
      queryClient.setQueryData(
        orderQueryKey({ path: { number: order.number } }),
        order,
      );
      setCartId(null);
      setOpen(false);
      void navigate(`/shop/orders/${order.number}`);
    },
    onError: (error) => {
      setProblem(errorDetail(error));
    },
  });

  const photos = new Map(
    (products.data ?? []).map((product) => [product.id, product.image_path]),
  );
  const count = cart?.lines.reduce((sum, line) => sum + line.quantity, 0) ?? 0;
  const busy = setLine.isPending || pay.isPending;
  const empty = cart === undefined || cart.lines.length === 0;
  const shownProblem =
    problem ?? (setLine.isError ? errorDetail(setLine.error) : null);

  return (
    <Sheet
      onOpenChange={(next) => {
        // A payment in flight keeps the cart open until PayPal answers.
        if (pay.isPending) return;
        if (next) setProblem(null);
        setOpen(next);
      }}
      open={open}
    >
      <SheetTrigger asChild>
        <Button
          aria-label={`Cart, ${String(count)} ${count === 1 ? "item" : "items"}`}
          className="relative"
          size="sm"
          variant="outline"
        >
          <ShoppingBag aria-hidden />
          {count > 0 && (
            <span
              aria-hidden
              className="absolute -top-1.5 -right-1.5 flex size-4.5 items-center justify-center rounded-full bg-primary text-[0.65rem] font-semibold text-primary-foreground tabular-nums"
            >
              {count}
            </span>
          )}
        </Button>
      </SheetTrigger>
      <SheetContent className="theme-shop flex w-full flex-col gap-0 sm:max-w-md">
        {pay.isPending && (
          <div
            className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-4 bg-card px-8 text-center"
            role="status"
          >
            <img alt="PayPal" className="h-7" src="/paypal-logo-blue.svg" />
            <LoaderCircle
              aria-hidden
              className="size-8 text-[#003087] motion-safe:animate-spin"
            />
            <div>
              <p className="text-lg font-semibold">Processing Your Payment</p>
              <p className="mt-1 text-sm text-muted-foreground">
                PayPal is authorizing and capturing{" "}
                {formatAmount(cart?.total.value ?? "0")} from{" "}
                {wallet?.email ?? "the saved wallet"}. This takes a few seconds;
                keep this window open.
              </p>
            </div>
          </div>
        )}
        <SheetHeader className="border-b">
          <SheetTitle>Your Cart</SheetTitle>
          <SheetDescription>
            {wallet === undefined
              ? "No PayPal wallet is linked to pay with."
              : `Paid from ${wallet.demo ? "the demo buyer's" : "your"} PayPal wallet, ${wallet.email}.`}
          </SheetDescription>
        </SheetHeader>
        <div className="flex-1 overflow-y-auto px-4">
          {empty ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 py-12 text-center">
              <ShoppingBag
                aria-hidden
                className="size-8 text-muted-foreground"
              />
              <p className="text-sm text-muted-foreground">
                Your cart is empty. Pick a size on a shoe and add it.
              </p>
            </div>
          ) : (
            <ul className="divide-y">
              {cart.lines.map((line) => (
                <li
                  className="flex gap-3 py-4"
                  key={`${line.product_id}/${line.size}`}
                >
                  <img
                    alt=""
                    className="size-16 shrink-0 rounded-md bg-muted object-cover"
                    src={photos.get(line.product_id)}
                  />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <div className="flex items-baseline justify-between gap-3">
                      <p className="truncate font-medium">
                        {line.brand} {line.name}
                      </p>
                      <p className="shrink-0 font-medium tabular-nums">
                        {formatAmount(line.line_total.value)}
                      </p>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      US {line.size}, {formatAmount(line.unit_price.value)} each
                    </p>
                    <div className="mt-2 flex items-center gap-1">
                      <Button
                        aria-label={`One fewer ${line.name}, size ${line.size}`}
                        disabled={busy || line.quantity === 1}
                        onClick={() => {
                          setLine.mutate({
                            ...line,
                            quantity: line.quantity - 1,
                          });
                        }}
                        size="icon-xs"
                        variant="outline"
                      >
                        <Minus aria-hidden />
                      </Button>
                      <span
                        aria-label={`Quantity ${String(line.quantity)}`}
                        className="w-7 text-center text-sm tabular-nums"
                      >
                        {line.quantity}
                      </span>
                      <Button
                        aria-label={`One more ${line.name}, size ${line.size}`}
                        disabled={busy || line.quantity >= 20}
                        onClick={() => {
                          setLine.mutate({
                            ...line,
                            quantity: line.quantity + 1,
                          });
                        }}
                        size="icon-xs"
                        variant="outline"
                      >
                        <Plus aria-hidden />
                      </Button>
                      <Button
                        aria-label={`Remove ${line.name}, size ${line.size}`}
                        className="ml-auto text-muted-foreground"
                        disabled={busy}
                        onClick={() => {
                          setLine.mutate({ ...line, quantity: 0 });
                        }}
                        size="xs"
                        variant="ghost"
                      >
                        <Trash2 aria-hidden />
                        Remove
                      </Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <SheetFooter className="border-t bg-secondary/60">
          <div className="flex items-baseline justify-between">
            <span className="font-medium">Total</span>
            <span className="text-lg font-semibold tabular-nums">
              {formatAmount(cart?.total.value ?? "0")}{" "}
              <span className="text-xs font-normal text-muted-foreground">
                USD
              </span>
            </span>
          </div>
          {shownProblem !== null && (
            <p className="text-sm text-destructive" role="alert">
              {shownProblem}
            </p>
          )}
          <Button
            aria-label="Pay with PayPal"
            className="h-11 bg-paypal-gold text-black hover:bg-paypal-gold-hover"
            disabled={busy || empty || wallet === undefined}
            onClick={() => {
              setProblem(null);
              if (!empty) pay.mutate(cart);
            }}
          >
            Pay with
            <img alt="" className="h-5" src="/paypal-logo-blue.svg" />
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            PayPal sandbox, test money. Nothing ships.
          </p>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
