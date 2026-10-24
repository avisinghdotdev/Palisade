import { Outlet } from "react-router";

import { AboutDialog } from "@/components/about-dialog";
import { ConsoleLogo } from "@/components/console-logo";
import { ConsoleNav } from "@/components/console-nav";
import { PalisadeBadge } from "@/components/palisade-badge";
import { ShopHeader } from "@/components/shop-header";
import { ShopLogo } from "@/components/shop-logo";
import { SurfaceLink } from "@/components/surface-link";

/** The merchant's side of Palisade: one header and page list around every console page. */
export default function Console() {
  return (
    <div className="theme-shop flex min-h-dvh flex-col bg-background text-foreground">
      <ShopHeader
        beside={
          <>
            <span aria-hidden className="h-5 w-px shrink-0 bg-border" />
            <span className="flex items-center gap-1.5 text-sm whitespace-nowrap text-muted-foreground">
              <ConsoleLogo className="size-4 shrink-0" />
              <span className="sr-only whitespace-nowrap lg:not-sr-only">
                Merchant Console
              </span>
            </span>
          </>
        }
        to="/console"
      >
        <PalisadeBadge>Powered by Palisade</PalisadeBadge>
        <SurfaceLink icon={<ShopLogo />} to="/shop">
          Shop
        </SurfaceLink>
        <AboutDialog title="About Merchant Console">
          <p>
            This is the merchant's side of Palisade for Ridgeline Running: every
            order with its PayPal IDs and payment history, every PayPal event
            the store receives, and in later milestones every agent, mandate,
            policy decision, and dispute with the evidence Palisade filed.
          </p>
        </AboutDialog>
      </ShopHeader>
      <ConsoleNav />
      <main className="flex-1 px-4 py-8 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
