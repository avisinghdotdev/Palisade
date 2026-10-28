import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import type { Cart } from "@/api/client";

import { statusOf } from "@/api-errors";
import { createCart, setCartLine } from "@/api/client";
import {
  cartOptions,
  cartQueryKey,
} from "@/api/client/@tanstack/react-query.gen";
import { useShopper } from "@/shopper";

/** The most pairs of one shoe in one size an order may hold; the API refuses more. */
export const MAX_PER_LINE = 5;
export const MAX_PER_LINE_NOTE = `Up to ${String(MAX_PER_LINE)} pairs of a size per order.`;

/** The browser's open cart, created on the first add with the wallet that pays. */
export function useCart(walletId: string | undefined) {
  const { cartId, setCartId } = useShopper();
  const queryClient = useQueryClient();
  const cart = useQuery({
    ...cartOptions({ path: { cart_id: cartId ?? "" } }),
    enabled: cartId !== null,
    // A cart that is gone stays gone; anything else is tried again before the bag shows empty.
    retry: (failures, error) => statusOf(error) !== 404 && failures < 3,
  });

  // A cart id the API no longer knows, or a cart already paid for, is forgotten. A failed read
  // forgets nothing, and neither does a payment still under way: a declined one reopens the cart.
  const stale =
    statusOf(cart.error) === 404 || cart.data?.status === "completed";
  useEffect(() => {
    if (stale) setCartId(null);
  }, [stale, setCartId]);

  const remember = (updated: Cart) => {
    setCartId(updated.id);
    queryClient.setQueryData(
      cartQueryKey({ path: { cart_id: updated.id } }),
      updated,
    );
  };

  const setLine = useMutation({
    mutationFn: async (change: {
      product_id: string;
      size: string;
      quantity: number;
    }) => {
      let id = cartId;
      if (id === null) {
        if (walletId === undefined) throw new Error("no wallet to pay with");
        const created = await createCart({
          body: { wallet_id: walletId },
          throwOnError: true,
        });
        id = created.data.id;
      }
      const updated = await setCartLine({
        path: { cart_id: id },
        body: change,
        throwOnError: true,
      });
      return updated.data;
    },
    onSuccess: remember,
    // A refused change means the cart is no longer what this browser thinks: checked out in
    // another tab, or gone. Reading it again lets the check above forget it.
    onError: async () => {
      if (cartId !== null) {
        await queryClient.invalidateQueries({
          queryKey: cartQueryKey({ path: { cart_id: cartId } }),
        });
      }
    },
  });

  return {
    cart: stale || cart.data?.status !== "open" ? undefined : cart.data,
    setLine,
  };
}
