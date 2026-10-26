import { useSyncExternalStore } from "react";

/**
 * What the browser remembers about its shopper: their linked wallet, who pays, the open cart,
 * the chat with the assistant, and the code that opens it.
 */
const WALLET_KEY = "palisade.wallet";
const PAYS_WITH_DEMO_KEY = "palisade.pays-with-demo";
const CART_KEY = "palisade.cart";
const PENDING_LINK_KEY = "palisade.pending-wallet-link";
const PASSKEY_KEY = "palisade.passkey";
const CONVERSATION_KEY = "palisade.conversation";
const CHAT_KEY_KEY = "palisade.chat-key";
const CHATS_KEY = "palisade.chats";
// Past chats the browser lists; older ones fall off the end.
const CHATS_KEPT = 20;

/**
 * A chat this browser had with the assistant: enough to list it and open it again. The key is
 * the one the API handed out when the chat started; nothing else opens the chat.
 */
export interface RememberedChat {
  id: string;
  key: string;
  walletId: string;
  title: string;
  at: string;
}

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

function isRememberedChat(value: unknown): value is RememberedChat {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof value.id === "string" &&
    "key" in value &&
    typeof value.key === "string" &&
    "walletId" in value &&
    typeof value.walletId === "string" &&
    "title" in value &&
    typeof value.title === "string" &&
    "at" in value &&
    typeof value.at === "string"
  );
}

/** The stored list, dropping anything an older build or a hand edit left in a different shape. */
function parseChats(raw: null | string): RememberedChat[] {
  if (raw === null) return [];
  const parsed: unknown = JSON.parse(raw);
  return Array.isArray(parsed) ? parsed.filter(isRememberedChat) : [];
}

// The setters read and write storage directly, so they keep one identity across renders and an
// effect that lists one never re-runs for it.
function rememberChat(chat: RememberedChat): void {
  const others = parseChats(localStorage.getItem(CHATS_KEY)).filter(
    (other) => other.id !== chat.id,
  );
  write(CHATS_KEY, JSON.stringify([chat, ...others].slice(0, CHATS_KEPT)));
}

function forgetChat(id: string): void {
  const kept = parseChats(localStorage.getItem(CHATS_KEY)).filter(
    (chat) => chat.id !== id,
  );
  write(CHATS_KEY, JSON.stringify(kept));
}

function setWalletId(id: string): void {
  write(WALLET_KEY, id);
}

function setPaysWithDemo(demo: boolean): void {
  write(PAYS_WITH_DEMO_KEY, demo ? "true" : null);
}

function setCartId(id: null | string): void {
  write(CART_KEY, id);
}

function setConversationId(id: null | string): void {
  write(CONVERSATION_KEY, id);
}

function setChatKey(key: null | string): void {
  write(CHAT_KEY_KEY, key);
}

export function useShopper() {
  const walletId = useStored(WALLET_KEY);
  const paysWithDemo = useStored(PAYS_WITH_DEMO_KEY) !== null;
  const cartId = useStored(CART_KEY);
  const conversationId = useStored(CONVERSATION_KEY);
  const chatKey = useStored(CHAT_KEY_KEY);
  const chats = parseChats(useStored(CHATS_KEY));
  return {
    /** The visitor's own linked wallet, kept while the demo buyer pays; null until one is linked. */
    walletId,
    /** True when the demo buyer pays: no wallet of their own, or they chose the demo buyer. */
    paysWithDemo: walletId === null || paysWithDemo,
    cartId,
    /** The chat with the assistant this browser is in the middle of; null before the first message. */
    conversationId,
    /** The code that opens the chat, kept so a reload does not ask for it again. */
    chatKey,
    /** This browser's chats, newest first. */
    chats,
    rememberChat,
    forgetChat,
    setWalletId,
    setPaysWithDemo,
    setCartId,
    setConversationId,
    setChatKey,
  };
}

/** The chat code for the API client, which sends it on every chat request. */
export function storedChatKey(): string | undefined {
  return localStorage.getItem(CHAT_KEY_KEY) ?? undefined;
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
