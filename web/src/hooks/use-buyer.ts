import { useQuery } from "@tanstack/react-query";

import {
  demoWalletOptions,
  walletOptions,
} from "@/api/client/@tanstack/react-query.gen";
import { useShopper } from "@/shopper";

/** The wallets this browser can pay with, and the one that pays: the visitor's own, or the demo buyer's. */
export function useBuyer() {
  const { walletId, paysWithDemo } = useShopper();
  // A missing wallet is a 404 that no retry will change.
  const own = useQuery({
    ...walletOptions({ path: { wallet_id: walletId ?? "" } }),
    enabled: walletId !== null,
    retry: false,
  });
  const demo = useQuery({ ...demoWalletOptions(), retry: false });
  const paying = paysWithDemo ? demo : own;
  return {
    wallet: paying.data,
    demoWallet: demo.data,
    ownWallet: own.data,
    paysWithDemo,
    isError: paying.isError,
  };
}
