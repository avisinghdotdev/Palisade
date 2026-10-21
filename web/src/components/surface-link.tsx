import type { ReactNode } from "react";

import { Link } from "react-router";

import { Button } from "@/components/ui/button";

/** A button that opens another surface of the demo in a new tab, with its mark. */
export function SurfaceLink({
  to,
  icon,
  children,
}: {
  to: string;
  icon: ReactNode;
  children: string;
}) {
  return (
    <Button asChild size="sm" variant="outline">
      <Link rel="noreferrer" target="_blank" to={to}>
        {icon}
        <span className="sr-only sm:not-sr-only">{children}</span>
        <span className="sr-only"> (opens in a new tab)</span>
      </Link>
    </Button>
  );
}
