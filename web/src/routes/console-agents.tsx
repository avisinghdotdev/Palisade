import { useQuery } from "@tanstack/react-query";
import { KeyRound, ShieldCheck } from "lucide-react";

import type { Policy, RegisteredAgent } from "@/api/client";

import {
  agentsOptions,
  policyOptions,
} from "@/api/client/@tanstack/react-query.gen";
import { AgentIcon } from "@/components/agent-icon";
import { CopyButton } from "@/components/copy-button";
import { DetailField } from "@/components/detail-field";
import { FilingMode } from "@/components/filing-mode";
import { Badge } from "@/components/ui/badge";
import { formatAmount, formatUtc } from "@/money";
import { pageTitle } from "@/page-title";

import type { Route } from "./+types/console-agents";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Agents and Policy", "Console") },
];

/** Whole cents as the console shows money: "$1,000,000.00". */
function cents(value: number): string {
  return formatAmount((value / 100).toFixed(2));
}

function AgentCard({
  agent,
  allowed,
}: {
  agent: RegisteredAgent;
  allowed: boolean;
}) {
  return (
    <li className="rounded-xl border bg-card px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <AgentIcon className="size-5 text-muted-foreground" />
        <h3 className="font-medium">{agent.name}</h3>
        <span className="font-mono text-xs text-muted-foreground">
          {agent.id}
        </span>
        <Badge
          className={
            allowed
              ? "border-emerald-600/40 bg-emerald-50 text-emerald-800"
              : "border-destructive/40 text-destructive"
          }
          variant="outline"
        >
          {allowed ? "Allowed by the policy" : "Not allowed"}
        </Badge>
      </div>
      <div className="mt-3">
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <KeyRound aria-hidden className="size-3.5" />
          Ed25519 public key, base64
        </p>
        <p className="mt-0.5 flex items-center gap-1">
          <span className="font-mono text-sm break-all">
            {agent.public_key}
          </span>
          <CopyButton
            label={`${agent.name}'s public key`}
            value={agent.public_key}
          />
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          Registered {formatUtc(agent.registered_at)}. Every request this agent
          sends is signed with the matching private key (RFC 9421) and checked
          against this one.
        </p>
      </div>
    </li>
  );
}

function PolicyPanel({ policy }: { policy: Policy }) {
  const { rules } = policy;
  return (
    <dl className="mt-1 divide-y">
      <DetailField
        label="Policy Hash (SHA-256)"
        note="Every mandate and agent order carries this hash; a mandate signed under another policy is refused."
        value={policy.hash}
      />
      <DetailField
        label="Allowed Agents"
        value={rules.allowed_agents.join(", ")}
      />
      <DetailField
        label="Cap per Order"
        mono={false}
        value={cents(rules.per_order_cap_cents)}
      />
      <DetailField
        label="Cap per Agent per Day"
        mono={false}
        value={cents(rules.per_agent_daily_cap_cents)}
      />
      <DetailField
        label="Blocked Categories"
        mono={false}
        value={
          rules.blocked_categories.length === 0
            ? null
            : rules.blocked_categories.join(", ")
        }
      />
    </dl>
  );
}

/** The agents the store trusts, and the rules every agent purchase passes. */
export default function ConsoleAgents() {
  const agents = useQuery(agentsOptions());
  const policy = useQuery(policyOptions());

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">
        Agents and Policy
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        The shopping agents the store knows, with the keys their signed requests
        are checked against, and the merchant&apos;s policy every agent purchase
        passes.
      </p>
      <div className="mt-8 grid items-start gap-10 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section aria-labelledby="agents">
          <h2 className="text-base font-semibold" id="agents">
            Registered Agents
          </h2>
          {agents.isPending && (
            <p className="mt-3 text-sm text-muted-foreground" role="status">
              Loading the agents…
            </p>
          )}
          {agents.isError && (
            <p className="mt-3 text-sm text-destructive">
              The agents did not load.
            </p>
          )}
          {agents.data !== undefined && (
            <ul className="mt-3 grid gap-3">
              {agents.data.map((agent) => (
                <AgentCard
                  agent={agent}
                  allowed={
                    policy.data?.rules.allowed_agents.includes(agent.id) ??
                    false
                  }
                  key={agent.id}
                />
              ))}
            </ul>
          )}
        </section>
        <aside
          aria-labelledby="policy"
          className="rounded-xl border bg-card px-5 py-4 lg:sticky lg:top-6"
        >
          <h2
            className="flex items-center gap-2 py-1 text-base font-semibold"
            id="policy"
          >
            <ShieldCheck aria-hidden className="size-4 text-muted-foreground" />
            Policy in Force
          </h2>
          {policy.isPending && (
            <p className="mt-2 text-sm text-muted-foreground" role="status">
              Loading the policy…
            </p>
          )}
          {policy.isError && (
            <p className="mt-2 text-sm text-destructive">
              The policy did not load.
            </p>
          )}
          {policy.data !== undefined && <PolicyPanel policy={policy.data} />}
          <div className="border-t pt-4 pb-1">
            <FilingMode />
          </div>
        </aside>
      </div>
    </>
  );
}
