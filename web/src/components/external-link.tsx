import type { ReactNode } from "react";

import { ArrowUpRight } from "lucide-react";

/** A link to another site: it opens in a new tab, and says so to the eye and to screen readers. */
export function ExternalLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <a
      className="inline-flex items-baseline gap-0.5 underline underline-offset-2 hover:text-foreground"
      href={href}
      rel="noreferrer"
      target="_blank"
    >
      {children}
      <ArrowUpRight aria-hidden className="size-3.5 shrink-0 self-center" />
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}
