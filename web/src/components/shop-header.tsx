import type { ReactNode } from "react";

import { Link } from "react-router";

import { ShopLogo } from "@/components/shop-logo";

/** The store's header: its name as a link, anything that belongs beside the name, and the page's actions. */
export function ShopHeader({
  to,
  beside,
  children,
}: {
  to: string;
  beside?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="border-b bg-card">
      <div className="flex items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            className="flex min-w-0 items-center gap-2 text-base font-semibold tracking-tight sm:text-lg"
            to={to}
          >
            <ShopLogo className="size-6 shrink-0 text-primary" />
            <span className="truncate">Ridgeline Running</span>
          </Link>
          {beside}
        </div>
        {children !== undefined && (
          <div className="flex shrink-0 items-center gap-2 sm:gap-3">
            {children}
          </div>
        )}
      </div>
    </header>
  );
}
