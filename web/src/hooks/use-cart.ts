import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import type { Cart } from "@/api/client";

import { createCart, setCartLine } from "@/api/client";
import {
  cartOptions,
  cartQueryKey,
} from "@/api/client/@tanstack/react-query.gen";
import { useShopper } from "@/shopper";

/** The browser's open cart, created on the first add with the wallet that pays. */
export function useCart(walletId: string | undefined) {
  const { cartId, setCartId } = useShopper();
  const queryClient = useQueryClient();
  const cart = useQuery({
    ...cartOptions({ path: { cart_id: cartId ?? "" } }),
    enabled: cartId !== null,
    retry: false,
  });

  // A cart id the API no longer knows, or a cart already turned into an order, is forgotten.
  const stale =
    cart.isError || (cart.data !== undefined && cart.data.status !== "open");
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

  return { cart: stale ? undefined : cart.data, setLine };
}
