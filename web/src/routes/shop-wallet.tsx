import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { confirmWalletLink, setCartWallet } from "@/api/client";
import { ShopHeader } from "@/components/shop-header";
import { ShopLogo } from "@/components/shop-logo";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/page-title";
import {
  errorDetail,
  LinkStartedElsewhere,
  pendingLink,
  useShopper,
} from "@/shopper";

import type { Route } from "./+types/shop-wallet";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Saving PayPal Wallet", "Shop") },
];

/** Where PayPal sends the buyer back after approving, or cancelling, the wallet link. */
export default function ShopWallet({ params }: Route.ComponentProps) {
  const { cartId, setWalletId, setPaysWithDemo, setCartId } = useShopper();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const confirm = useMutation({
    mutationFn: async (approvedTokenId: null | string) => {
      const linked = await confirmWalletLink({
        body: { setup_token_id: pendingLink.take(approvedTokenId) },
        throwOnError: true,
      });
      return linked.data;
    },
    onSuccess: async (wallet) => {
      // Linking the demo buyer's own account only makes the demo buyer pay again.
      if (!wallet.demo) setWalletId(wallet.id);
      setPaysWithDemo(wallet.demo);
      if (cartId !== null) {
        // The wallet is saved whatever happens to the cart. A cart that cannot follow it, such
        // as one checked out meanwhile, is left behind rather than left paying with the old wallet.
        const moved = await setCartWallet({
          path: { cart_id: cartId },
          body: { wallet_id: wallet.id },
        });
        if (moved.error !== undefined) setCartId(null);
      }
      await navigate("/shop", { replace: true });
    },
  });

  // The pending link is taken once on arrival; a later render must not act on its absence.
  const arrivedRef = useRef(false);
  const { mutate } = confirm;
  useEffect(() => {
    if (arrivedRef.current) return;
    arrivedRef.current = true;
    if (params.outcome === "return") {
      mutate(search.get("approval_token_id"));
    } else {
      pendingLink.clear();
      void navigate("/shop", { replace: true });
    }
  }, [mutate, navigate, params.outcome, search]);

  return (
    <div className="theme-shop flex min-h-dvh flex-col bg-secondary text-foreground">
      <ShopHeader to="/shop" />
      <main className="flex flex-1 items-center justify-center px-4 py-10">
        {confirm.isError ? (
          <section className="w-full max-w-sm rounded-xl border bg-card p-8 text-center shadow-sm">
            <h1 className="text-lg font-semibold tracking-tight">
              Wallet not Linked
            </h1>
            <p className="mt-2 text-sm text-destructive" role="alert">
              {confirm.error instanceof LinkStartedElsewhere
                ? confirm.error.message
                : errorDetail(confirm.error)}
            </p>
            <Button asChild className="mt-6">
              <Link to="/shop">Back to the Shop</Link>
            </Button>
          </section>
        ) : (
          // A cancelled link goes straight back to the shop, so only an approval shows progress.
          params.outcome === "return" && (
            <section
              className="w-full max-w-sm rounded-xl border bg-card p-8 text-center shadow-sm motion-safe:animate-in motion-safe:fade-in motion-safe:zoom-in-95"
              role="status"
            >
              <WalletTransfer />
              <h1 className="mt-6 text-lg font-semibold tracking-tight">
                Saving Your PayPal Wallet
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                PayPal approved the link. Ridgeline Running is saving it so
                later orders pay without leaving the shop.
              </p>
            </section>
          )
        )}
      </main>
    </div>
  );
}

// The delay sits inside the shorthand: a separate animation-delay class loses to it.
const RELAY_DOTS = [
  "motion-safe:animate-[relay_1.2s_ease-in-out_infinite]",
  "motion-safe:animate-[relay_1.2s_ease-in-out_150ms_infinite]",
  "motion-safe:animate-[relay_1.2s_ease-in-out_300ms_infinite]",
  "motion-safe:animate-[relay_1.2s_ease-in-out_450ms_infinite]",
];

/** PayPal's mark and the shop's, with dots that light up in turn from PayPal to the shop. */
function WalletTransfer() {
  return (
    <div aria-hidden className="flex items-center justify-center gap-3">
      <span className="flex size-12 items-center justify-center rounded-full border bg-white">
        <img alt="" className="size-6" src="/paypal-mark.svg" />
      </span>
      <span className="flex gap-1.5">
        {RELAY_DOTS.map((animation) => (
          <span
            className={`size-1.5 rounded-full bg-[#009cde] opacity-60 ${animation}`}
            key={animation}
          />
        ))}
      </span>
      <span className="flex size-12 items-center justify-center rounded-full border bg-white">
        <ShopLogo className="size-6 text-primary" />
      </span>
    </div>
  );
}
