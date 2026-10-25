import type { Activity, ToolCallEvent } from "@/api/client";

import { formatAmount } from "@/money";
import { TOOL_LABELS } from "@/tool-labels";

/** How each agent-side event reads to a merchant. */
export const ACTIVITY_LABELS: Record<Activity["kind"], string> = {
  conversation_started: "Chat Started",
  shopper_message: "Shopper Wrote",
  assistant_tool_call: "Agent Acted",
  signature_accepted: "Signed Request Accepted",
  signature_refused: "Signed Request Refused",
  mandate_requested: "Mandate Requested",
  mandate_signed: "Mandate Signed",
  mandate_declined: "Mandate Declined",
  mandate_expired: "Mandate Expired",
  policy_refused: "Policy Refused the Purchase",
  mandate_refused: "Mandate Refused the Purchase",
  checkout_accepted: "Checkout Accepted",
};

/** Who did it, as the timeline names them: the shopper, the buyer signing, the store, or the agent. */
export function activitySource(row: Activity): string {
  switch (row.kind) {
    case "conversation_started":
    case "shopper_message":
      return "Shopper";
    case "mandate_signed":
    case "mandate_declined":
      return "Buyer";
    case "mandate_expired":
    case "policy_refused":
    case "mandate_refused":
    case "checkout_accepted":
    case "signature_refused":
      return "Palisade";
    case "assistant_tool_call":
    case "signature_accepted":
    case "mandate_requested":
      return row.agent?.name ?? "Agent";
  }
}

const SIGNERS: Record<string, string> = {
  passkey: "the buyer's passkey",
  demo_signer: "the demo signer (simulated)",
};

function text(details: Activity["details"], key: string): string {
  const value = details[key];
  return typeof value === "string" ? value : "";
}

function list(details: Activity["details"], key: string): string {
  const value = details[key];
  return Array.isArray(value) ? value.map(String).join(", ") : "";
}

function isTool(value: string): value is ToolCallEvent["tool"] {
  return value in TOOL_LABELS;
}

/** One line on what the event was about, from the details the API stored with it. */
export function activityDetail(row: Activity): string {
  const { details } = row;
  switch (row.kind) {
    case "conversation_started":
      return details["with_cart"] === true
        ? "Filling the shopper's open cart"
        : "A fresh cart";
    case "shopper_message":
      return text(details, "text");
    case "assistant_tool_call": {
      const tool = text(details, "tool");
      const label = isTool(tool) ? TOOL_LABELS[tool] : tool;
      return `${label}: ${text(details, "summary")}`;
    }
    case "signature_accepted":
      return `${text(details, "method")} ${text(details, "route")}`;
    case "signature_refused":
      return `${text(details, "method")} ${text(details, "route")}: ${text(details, "reason")}`;
    case "mandate_requested": {
      const categories = list(details, "categories");
      return `${text(details, "mandate_kind")} mandate, up to ${formatAmount(text(details, "cap"))}${categories === "" ? "" : `, ${categories}`}`;
    }
    case "mandate_signed":
      return `Signed with ${SIGNERS[text(details, "signed_by")] ?? text(details, "signed_by")}`;
    case "mandate_declined":
      return "The buyer declined on the consent page";
    case "mandate_expired":
      return `Ran out while ${text(details, "was")}`;
    case "policy_refused":
    case "mandate_refused":
      return list(details, "reasons");
    case "checkout_accepted":
      return `Order ${text(details, "number")} placed under the mandate`;
  }
}
