import { useSyncExternalStore } from "react";

/** What the browser remembers about its shopper: their linked wallet, who pays, and the open cart. */
const WALLET_KEY = "palisade.wallet";
const PAYS_WITH_DEMO_KEY = "palisade.pays-with-demo";
const CART_KEY = "palisade.cart";
const PENDING_LINK_KEY = "palisade.pending-wallet-link";
const PASSKEY_KEY = "palisade.passkey";

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function write(key: string, value: null | string) {
  if (value === null) localStorage.removeItem(key);
  else localStorage.setItem(key, value);
  for (const listener of listeners) listener();
}

function useStored(key: string): null | string {
  // The prerendered page knows no shopper; the real value arrives once React runs in the browser.
  return useSyncExternalStore(
    subscribe,
    () => localStorage.getItem(key),
    () => null,
  );
}

export function useShopper() {
  const walletId = useStored(WALLET_KEY);
  const paysWithDemo = useStored(PAYS_WITH_DEMO_KEY) !== null;
  const cartId = useStored(CART_KEY);
  return {
    /** The visitor's own linked wallet, kept while the demo buyer pays; null until one is linked. */
    walletId,
    /** True when the demo buyer pays: no wallet of their own, or they chose the demo buyer. */
    paysWithDemo: walletId === null || paysWithDemo,
    cartId,
    setWalletId: (id: string) => {
      write(WALLET_KEY, id);
    },
    setPaysWithDemo: (demo: boolean) => {
      write(PAYS_WITH_DEMO_KEY, demo ? "true" : null);
    },
    setCartId: (id: null | string) => {
      write(CART_KEY, id);
    },
  };
}

/** PayPal came back with a wallet link this browser, at this web address, did not start. */
export class LinkStartedElsewhere extends Error {
  constructor() {
    super(
      "This wallet link was started in another browser or at another web address, so it cannot be finished here. Start it again from the Who Pays menu in the shop.",
    );
  }
}

/**
 * The setup token of a wallet link in progress, kept across the round trip to PayPal. Local
 * storage, not session storage, so a return that opens in a new tab still finds it.
 */
export const pendingLink = {
  set(token: string) {
    localStorage.setItem(PENDING_LINK_KEY, token);
  },
  /** The started token when PayPal returned that same one; the pending link is cleared either way. */
  take(approved: null | string): string {
    const started = localStorage.getItem(PENDING_LINK_KEY);
    localStorage.removeItem(PENDING_LINK_KEY);
    if (started === null || started !== approved) {
      throw new LinkStartedElsewhere();
    }
    return started;
  },
  clear() {
    localStorage.removeItem(PENDING_LINK_KEY);
  },
};

/** The passkey this browser registered to sign mandates; the API knows it by this id. */
export const passkey = {
  get(): null | string {
    return localStorage.getItem(PASSKEY_KEY);
  },
  set(id: string) {
    localStorage.setItem(PASSKEY_KEY, id);
  },
};

/** The message FastAPI put in an error response, or a plain fallback for anything else. */
export function errorDetail(error: unknown): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "detail" in error &&
    typeof error.detail === "string"
  ) {
    return error.detail;
  }
  return "Something went wrong. Please try again.";
}
