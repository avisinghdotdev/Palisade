import type { ReactNode } from "react";

import { Link } from "react-router";

import { Button } from "@/components/ui/button";

/** A button that moves to another surface of the demo, with its mark. */
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
      <Link to={to}>
        {icon}
        <span className="sr-only whitespace-nowrap lg:not-sr-only">
          {children}
        </span>
      </Link>
    </Button>
  );
}
