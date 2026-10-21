import { Link } from "react-router";

import { AboutDialog } from "@/components/about-dialog";
import { ConsoleLogo } from "@/components/console-logo";
import { HackathonLink } from "@/components/hackathon-link";
import { PalisadeLogo } from "@/components/palisade-logo";
import { ShopLogo } from "@/components/shop-logo";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import type { Route } from "./+types/home";

export const links: Route.LinksFunction = () => [
  { rel: "icon", href: "/favicon-palisade.svg", type: "image/svg+xml" },
];

const STEPS = [
  {
    title: "The buyer signs a mandate",
    body: "An AI shopping agent proposes a cart. The buyer approves it on Palisade's consent page with a passkey, which produces a signed record of the items, the amount cap, the merchant, and the expiry.",
  },
  {
    title: "Palisade checks the agent",
    body: "The agent signs its requests with its own key. Palisade verifies the signature and applies the merchant's policy: which agents may buy, spend caps, and the amount above which the buyer must approve again.",
  },
  {
    title: "The mandate is bound to the PayPal order",
    body: "Palisade creates the PayPal order with the mandate's id and hash in its custom id, then captures the payment once the checks pass. Every order is tagged as placed by an agent or by a person.",
  },
  {
    title: "Disputes are answered with evidence",
    body: "When PayPal sends a dispute webhook, Palisade routes it by reason, assembles the mandate record, tracking, and policy checks, has Claude write the narrative, and files it through the PayPal Disputes API.",
  },
];

export default function Home() {
  return (
    <div className="theme-palisade flex min-h-dvh flex-col bg-background text-foreground">
      <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <p className="flex items-center gap-2 text-lg font-semibold text-primary">
          <PalisadeLogo className="size-7" />
          Palisade
        </p>
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span>
            An entry in the <HackathonLink />
          </span>
          <span className="inline-flex rounded-md border bg-card px-2.5 py-1 text-xs font-medium">
            v{__APP_VERSION__}
          </span>
          <AboutDialog title="About Palisade">
            <p>
              Palisade is a merchant-side trust and dispute layer for purchases
              that AI agents make with PayPal. This site is the hosted demo: a
              shop that agents can buy from, and the merchant console behind it.
            </p>
            <p>
              The code is the team's entry to the hackathon. More of the loop
              lands with each milestone up to the submission on 12 November
              2026.
            </p>
          </AboutDialog>
        </div>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
        <h1 className="text-3xl font-semibold tracking-tight">
          Trust and dispute defense for purchases that AI agents make with
          PayPal
        </h1>
        <p className="mt-3 text-muted-foreground">
          When an agent buys on a person's behalf, the merchant is left to prove
          what the person allowed. Palisade keeps that proof: a mandate the
          buyer signed, a verified agent, and a PayPal order that carries both.
          When a dispute arrives, the evidence is filed automatically.
        </p>

        <h2 className="mt-12 text-xl font-semibold tracking-tight">
          How it works
        </h2>
        <ol className="mt-4 space-y-4">
          {STEPS.map((step, index) => (
            <li className="flex gap-4" key={step.title}>
              <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">
                {index + 1}
              </span>
              <div>
                <h3 className="font-medium">{step.title}</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {step.body}
                </p>
              </div>
            </li>
          ))}
        </ol>

        <h2 className="mt-12 text-xl font-semibold tracking-tight">
          For merchants
        </h2>
        <p className="mt-3 text-sm text-muted-foreground">
          Palisade sits in front of a merchant's PayPal checkout. The merchant
          points agents at Palisade's endpoint for its catalog and checkout,
          lets its PayPal app send webhooks to Palisade, and sets its policy in
          the console. From then on every agent order carries a mandate, and
          every dispute on one is answered with it. In this demo Palisade is
          wired to one store, Ridgeline Running.
        </p>

        <h2 className="mt-12 text-xl font-semibold tracking-tight">
          See it in action
        </h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Link
            className="rounded-xl focus-visible:outline-2"
            rel="noreferrer"
            target="_blank"
            to="/shop"
          >
            <Card className="theme-shop h-full transition-colors hover:bg-accent/60">
              <CardHeader>
                <ShopLogo className="size-6 text-primary" />
                <CardTitle>Ridgeline Running</CardTitle>
                <CardDescription>
                  The demo shop. Browse the shoes, then buy yourself or through
                  an agent.
                  <span className="sr-only"> (opens in a new tab)</span>
                </CardDescription>
              </CardHeader>
            </Card>
          </Link>
          <Link
            className="rounded-xl focus-visible:outline-2"
            rel="noreferrer"
            target="_blank"
            to="/merchant"
          >
            <Card className="theme-shop h-full transition-colors hover:bg-accent/60">
              <CardHeader>
                <ConsoleLogo className="size-6 text-primary" />
                <CardTitle>Merchant console</CardTitle>
                <CardDescription>
                  What the store sees: every PayPal event, and soon every order,
                  agent, and dispute.
                  <span className="sr-only"> (opens in a new tab)</span>
                </CardDescription>
              </CardHeader>
            </Card>
          </Link>
        </div>
      </main>
    </div>
  );
}
