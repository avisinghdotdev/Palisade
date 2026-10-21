import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Links, Meta, Outlet, Scripts, ScrollRestoration } from "react-router";

import { TooltipProvider } from "@/components/ui/tooltip";

import type { Route } from "./+types/root";

import "./app.css";

const siteTitle = "Palisade";
const queryClient = new QueryClient();

export const meta: Route.MetaFunction = () => [
  { title: siteTitle },
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
