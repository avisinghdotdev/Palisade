import { useSyncExternalStore } from "react";

function subscribe() {
  return () => {
    // Nothing changes after hydration, so there is nothing to unsubscribe.
  };
}

/** False during prerendering and the hydration render, true once React runs in the browser.
 *
 * Markup that depends on browser-only state, such as the URL's query string, must render the
 * prerendered form first: React does not patch attribute differences while hydrating. */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
