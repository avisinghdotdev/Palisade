import { PalisadeLogo } from "@/components/palisade-logo";
import { Badge } from "@/components/ui/badge";

/** Palisade's mark in its own colours, wherever the merchant's product is named inside the shop.
 *
 * On narrow screens only the mark shows; the words stay for screen readers. */
export function PalisadeBadge({ children }: { children: string }) {
  return (
    <Badge
      className="theme-palisade h-6 shrink-0 border-primary/30 bg-card px-2 text-primary sm:px-2.5"
      variant="outline"
    >
      <span className="flex size-4 items-center">
        <PalisadeLogo className="size-4" />
      </span>
      <span className="sr-only sm:not-sr-only">{children}</span>
    </Badge>
  );
}
