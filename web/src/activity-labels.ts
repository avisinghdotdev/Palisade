import type { Activity, ToolCallEvent } from "@/api/client";
import type { TimelineEntry } from "@/components/timeline";

import { outcomeLabel, reasonLabel, statusLabel } from "@/dispute-labels";
import { formatAmount } from "@/money";
import { TOOL_LABELS } from "@/tool-labels";

/** How each agent-side and dispute event reads to a merchant. */
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
  dispute_opened: "Dispute Received from PayPal",
  dispute_updated: "PayPal Updated the Dispute",
  dispute_resolved: "PayPal Resolved the Dispute",
  sandbox_dispute_filed: "Dispute Filed as the Demo Buyer",
  sandbox_dispute_adjudicated: "Decision Requested from PayPal",
};

/** What made Palisade read a dispute from PayPal. */
const DISPUTE_READS: Record<string, string> = {
  webhook: "PayPal Webhook",
  reconciliation: "Hourly Check with PayPal",
  console: "Merchant Console",
  simulator: "Sandbox Simulator",
};

/** Who did it, as the timeline names them: the shopper, the buyer signing, the store, the agent, or PayPal. */
export function activitySource(row: Activity): string {
  switch (row.kind) {
    case "dispute_opened":
    case "dispute_updated":
    case "dispute_resolved": {
      const read = text(row.details, "source");
      return DISPUTE_READS[read] ?? read;
    }
    case "sandbox_dispute_filed":
    case "sandbox_dispute_adjudicated":
      return "Sandbox Simulator";
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

const STAGES: Record<string, string> = {
  INQUIRY: "an inquiry",
  CHARGEBACK: "a claim",
  PRE_ARBITRATION: "pre-arbitration",
  ARBITRATION: "arbitration",
};

/** The dispute's new status, and its new stage when it moved to one. */
function disputeUpdate(details: Activity["details"]): string {
  const status = statusLabel(text(details, "status"));
  const stage = text(details, "stage");
  return stage === text(details, "from_stage")
    ? `now ${status}`
    : `now ${status}, as ${STAGES[stage] ?? stage}`;
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
    case "dispute_opened":
      return `${row.dispute_id ?? ""}: ${reasonLabel(text(details, "reason"))}, ${formatAmount(text(details, "amount"))}, ${statusLabel(text(details, "status"))}`;
    case "dispute_updated":
      return `${row.dispute_id ?? ""}: ${disputeUpdate(details)}`;
    case "dispute_resolved": {
      const outcome = text(details, "outcome");
      return `${row.dispute_id ?? ""}: ${outcome === "" ? "closed with no outcome given" : outcomeLabel(outcome)}`;
    }
    case "sandbox_dispute_filed":
      return `${reasonLabel(text(details, "reason"))}, through the demo buyer's own PayPal app`;
    case "sandbox_dispute_adjudicated":
      return text(details, "outcome") === "SELLER_FAVOR"
        ? "Asked PayPal's sandbox to decide for the seller"
        : "Asked PayPal's sandbox to decide for the buyer";
  }
}

/** An activity row as a timeline shows it. */
export function activityEntry(row: Activity): TimelineEntry {
  return {
    key: row.id,
    at: row.at,
    label: ACTIVITY_LABELS[row.kind],
    source: activitySource(row),
    detail: activityDetail(row),
    code: false,
  };
}
