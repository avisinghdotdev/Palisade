import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { setCartWallet, startWalletLink } from "@/api/client";
import { cartQueryKey } from "@/api/client/@tanstack/react-query.gen";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useBuyer } from "@/hooks/use-buyer";
import { errorDetail, pendingLink, useShopper } from "@/shopper";

const DEMO = "demo";
const OWN = "own";

/** Who pays: the pre-linked demo buyer, or a sandbox PayPal wallet the visitor links once. */
export function BuyerMenu() {
  const { walletId, cartId, setPaysWithDemo } = useShopper();
  const buyer = useBuyer();
  const queryClient = useQueryClient();
  const [problem, setProblem] = useState<null | string>(null);

  const link = useMutation({
    mutationFn: async () => {
      const started = await startWalletLink({ throwOnError: true });
      return started.data;
    },
    onSuccess: (started) => {
      pendingLink.set(started.setup_token_id);
      window.location.assign(started.approve_url);
    },
    onError: (error) => {
      setProblem(errorDetail(error));
    },
  });

  // An open cart pays with whoever is chosen, so it follows the choice.
  const moveCart = useMutation({
    mutationFn: async ({ cart, wallet }: { cart: string; wallet: string }) => {
      const updated = await setCartWallet({
        path: { cart_id: cart },
        body: { wallet_id: wallet },
        throwOnError: true,
      });
      return updated.data;
    },
    onSuccess: (cart) => {
      queryClient.setQueryData(
        cartQueryKey({ path: { cart_id: cart.id } }),
        cart,
      );
    },
    onError: (error) => {
      setProblem(errorDetail(error));
    },
  });

  const choose = (value: string) => {
    const demo = value === DEMO;
    setPaysWithDemo(demo);
    setProblem(null);
    const wallet = demo ? buyer.demoWallet?.id : walletId;
    if (cartId !== null && wallet) moveCart.mutate({ cart: cartId, wallet });
  };

  // Back from PayPal can restore this page as it was left, still opening PayPal.
  const { reset } = link;
  useEffect(() => {
    const restored = (event: PageTransitionEvent) => {
      if (event.persisted) reset();
    };
    window.addEventListener("pageshow", restored);
    return () => {
      window.removeEventListener("pageshow", restored);
    };
  }, [reset]);

  const ownLabel = buyer.ownWallet?.email ?? "Your Sandbox PayPal";
  // PayPal takes a second or two to create the link, and the page then leaves for PayPal.
  const opening = link.isPending || link.isSuccess;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="outline">
          <img alt="" className="size-4" src="/paypal-mark.svg" />
          <span className="sr-only whitespace-nowrap lg:not-sr-only lg:max-w-48 lg:truncate">
            Buying as{" "}
            {buyer.paysWithDemo
              ? "Demo Buyer"
              : (buyer.ownWallet?.first_name ?? ownLabel)}
          </span>
          <ChevronDown aria-hidden className="opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72" collisionPadding={16}>
        <DropdownMenuLabel>Who Pays</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          onValueChange={choose}
          value={buyer.paysWithDemo ? DEMO : OWN}
        >
          <DropdownMenuRadioItem value={DEMO}>
            Demo Buyer (Pre-linked)
          </DropdownMenuRadioItem>
          {walletId !== null && (
            <DropdownMenuRadioItem value={OWN}>
              {ownLabel}
            </DropdownMenuRadioItem>
          )}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={opening}
          onSelect={(event) => {
            // The menu stays open so the progress below stays in view.
            event.preventDefault();
            setProblem(null);
            link.mutate();
          }}
        >
          {opening ? (
            <>
              <LoaderCircle aria-hidden className="motion-safe:animate-spin" />
              <span role="status">Opening PayPal…</span>
            </>
          ) : walletId === null ? (
            "Link My Own Sandbox PayPal…"
          ) : (
            "Link a Different Sandbox PayPal…"
          )}
        </DropdownMenuItem>
        {problem === null ? (
          <p className="px-2 pt-1 pb-2 text-xs text-muted-foreground">
            Approve once on the PayPal sandbox. Later orders pay without leaving
            the shop.
          </p>
        ) : (
          <p className="px-2 pt-1 pb-2 text-xs text-destructive" role="alert">
            {problem}
          </p>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
