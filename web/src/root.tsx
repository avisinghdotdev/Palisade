import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  isRouteErrorResponse,
  Link,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
} from "react-router";

import { PalisadeLogo } from "@/components/palisade-logo";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useHydrated } from "@/hooks/use-hydrated";

import type { Route } from "./+types/root";

import "./app.css";

const siteTitle = "Palisade";
const queryClient = new QueryClient();

// The store's mark on every page, the SPA fallback included, so no browser asks for
// /favicon.ico. The landing page adds Palisade's mark after it, and the last icon wins.
export const links: Route.LinksFunction = () => [
  { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
];

// React Router passes null, not undefined, when nothing failed.
export const meta: Route.MetaFunction = ({ error }) => [
  {
    title:
      error === null || error === undefined
        ? siteTitle
        : isRouteErrorResponse(error) && error.status === 404
          ? "Page not Found"
          : "Something went Wrong",
  },
  { property: "og:title", content: siteTitle },
  { property: "og:type", content: "website" },
];

export function Layout({ children }: { children: ReactNode }) {
  return (
    <html className="h-full" lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta content="width=device-width, initial-scale=1" name="viewport" />
        <Meta />
        <Links />
      </head>
      <body className="min-h-full bg-background text-foreground antialiased">
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <Outlet />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

/** Any address the demo does not know, or a page that failed, with the ways back. */
export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  const missing = isRouteErrorResponse(error) && error.status === 404;
  // The SPA fallback page arrives with an empty body; matching it while React starts avoids a
  // hydration mismatch, and the page appears on the next render.
  const hydrated = useHydrated();
  if (!hydrated) return null;

  return (
    <main className="theme-palisade flex min-h-dvh flex-col items-center justify-center bg-background px-6 text-center text-foreground">
      <PalisadeLogo className="size-10 text-primary" />
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">
        {missing ? "Page not Found" : "Something went Wrong"}
      </h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        {missing
          ? "There is nothing at this address. The demo has three places to go:"
          : "This page failed to load. Reload it, or start from one of these:"}
      </p>
      <nav aria-label="Demo pages" className="mt-6">
        <ul className="flex flex-wrap justify-center gap-3 text-sm font-medium">
          <li>
            <Link
              className="rounded-md border bg-card px-3 py-2 hover:bg-accent"
              to="/"
            >
              Palisade Home
            </Link>
          </li>
          <li>
            <Link
              className="rounded-md border bg-card px-3 py-2 hover:bg-accent"
              to="/shop"
            >
              Ridgeline Running Shop
            </Link>
          </li>
          <li>
            <Link
              className="rounded-md border bg-card px-3 py-2 hover:bg-accent"
              to="/console"
            >
              Merchant Console
            </Link>
          </li>
        </ul>
      </nav>
    </main>
  );
}
