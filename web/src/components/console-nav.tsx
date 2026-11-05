import { Link, useLocation } from "react-router";

import { useHydrated } from "@/hooks/use-hydrated";

/** Each page and the paths that belong to it: an order's own page sits under Orders. */
const PAGES = [
  {
    to: "/console",
    label: "Orders",
    owns: (path: string) =>
      path === "/console" || path.startsWith("/console/orders/"),
  },
  {
    to: "/console/disputes",
    label: "Disputes",
    owns: (path: string) => path.startsWith("/console/disputes"),
  },
  {
    to: "/console/agents",
    label: "Agents and Policy",
    owns: (path: string) => path.startsWith("/console/agents"),
  },
  {
    to: "/console/analytics",
    label: "Analytics",
    owns: (path: string) => path.startsWith("/console/analytics"),
  },
  {
    to: "/console/activity",
    label: "Activity",
    owns: (path: string) => path.startsWith("/console/activity"),
  },
];

/** The console's pages, with the current one marked. */
export function ConsoleNav() {
  const { pathname } = useLocation();
  // The prerendered markup marks no tab; the current one is marked once React runs.
  const hydrated = useHydrated();

  return (
    <nav aria-label="Console pages" className="border-b bg-card">
      <ul className="flex gap-1 overflow-x-auto px-4 sm:px-6 lg:px-8">
        {PAGES.map((page) => {
          const current = hydrated && page.owns(pathname);
          return (
            <li key={page.to}>
              <Link
                aria-current={current ? "page" : undefined}
                className={`-mb-px inline-block border-b-2 px-3 py-2.5 text-sm font-medium whitespace-nowrap ${
                  current
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
                to={page.to}
              >
                {page.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
