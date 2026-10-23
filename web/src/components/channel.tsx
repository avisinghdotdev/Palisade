import type { Order } from "@/api/client";

/** A person, drawn in the current text colour with a soft fill. */
function HumanIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden className={className} fill="none" viewBox="0 0 24 24">
      <path
        d="M17.35 7.9a5.35 5.35 0 1 1-10.7 0a5.35 5.35 0 0 1 10.7 0"
        fill="currentColor"
        opacity=".2"
      />
      <path
        d="M20.3 21.72H3.7a8.4 8.4 0 0 1 8.3-8.46c4.59 0 8.3 3.79 8.3 8.46"
        fill="currentColor"
        opacity=".2"
      />
      <path
        d="M17.35 7.9A5.33 5.33 0 0 1 12 13.27a5.35 5.35 0 1 1 5.35-5.35M20.3 21.56a8.3 8.3 0 0 0-16.6 0"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

/** A shopping agent's robot head, drawn in the current text colour with a soft fill. */
function AgentIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden className={className} fill="none" viewBox="0 0 24 24">
      <rect
        fill="currentColor"
        height="12.76"
        opacity=".2"
        rx="3"
        width="16.4"
        x="3.8"
        y="7.11"
      />
      <path
        d="M9.3 11.48v2.64m5.65-2.64v2.64m5.25-.39h2.12m-20.65 0H3.8m8.5-6.62c0-1.35 0-2.03-.32-2.52a2 2 0 0 0-.61-.6c-.5-.32-1.17-.32-2.52-.32h-.21"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
      <rect
        height="12.76"
        rx="3"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
        width="16.4"
        x="3.8"
        y="7.11"
      />
    </svg>
  );
}

/** Who placed an order: the buyer at the checkout, or a shopping agent under a mandate. */
export function Channel({ channel }: { channel: Order["channel"] }) {
  const Icon = channel === "agent" ? AgentIcon : HumanIcon;
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon className="size-4 shrink-0 text-primary" />
      {channel === "agent" ? "Agent" : "Person"}
    </span>
  );
}
