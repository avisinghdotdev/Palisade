import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Meta, Outlet, Scripts, ScrollRestoration } from "react-router";

import type { Route } from "./+types/root";

const siteTitle = "Palisade";
const queryClient = new QueryClient();

export const meta: Route.MetaFunction = () => [
  { title: siteTitle },
  { property: "og:title", content: siteTitle },
  { property: "og:type", content: "website" },
];

export function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta content="width=device-width, initial-scale=1" name="viewport" />
        <Meta />
      </head>
      <body>
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
      <Outlet />
    </QueryClientProvider>
  );
}
