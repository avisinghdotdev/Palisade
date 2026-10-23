import { PalisadeLogo } from "@/components/palisade-logo";
import { Badge } from "@/components/ui/badge";

/** Palisade's mark in its own colours, wherever the merchant's product is named inside the shop.
 *
 * Phone and tablet headers have no room for it, so it shows from the large breakpoint up. */
export function PalisadeBadge({ children }: { children: string }) {
  return (
    <span className="hidden lg:inline-flex">
      <Badge
        className="theme-palisade h-6 shrink-0 border-primary/30 bg-card px-2.5 text-primary"
        variant="outline"
      >
        <span className="flex size-4 items-center">
          <PalisadeLogo className="size-4" />
        </span>
        {children}
      </Badge>
    </span>
  );
}
