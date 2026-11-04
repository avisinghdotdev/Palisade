import type { Activity, ToolCallEvent } from "@/api/client";
import type { TimelineEntry } from "@/components/timeline";

import {
  evidenceTypeLabel,
  outcomeLabel,
  reasonLabel,
  statusLabel,
} from "@/dispute-labels";
import { formatAmount, formatUtc } from "@/money";
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
  evidence_prepared: "Evidence Prepared",
  dispute_escalated: "Escalated to a Claim",
  evidence_filed: "Evidence Filed with PayPal",
  evidence_refused: "PayPal Refused the Evidence",
  dispute_flagged: "Needs the Merchant",
  alert_refund_scheduled: "Alert Refund Scheduled",
  alert_refund_cancelled: "Alert Refund Cancelled",
  alert_refund_skipped: "Alert Refund Left to the Merchant",
  alert_refunded: "Refunded to End the Alert",
  alert_refund_refused: "PayPal Refused the Refund",
};

/** What made Palisade read a dispute from PayPal. */
const DISPUTE_READS: Record<string, string> = {
  webhook: "PayPal Webhook",
  reconciliation: "Hourly Check with PayPal",
  console: "Merchant Console",
  simulator: "Sandbox Simulator",
  filing: "Evidence Filing",
  alert: "Alert Refund Timer",
};

/** Who refunded a pre-chargeback alert: the merchant, or Palisade when the time came. */
const REFUNDERS: Record<string, string> = {
  console: "Merchant Console",
  timer: "Palisade",
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
    case "alert_refunded":
    case "alert_refund_refused": {
      const refunder = text(row.details, "source");
      return REFUNDERS[refunder] ?? refunder;
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
    case "evidence_prepared":
    case "dispute_escalated":
    case "evidence_filed":
    case "evidence_refused":
    case "dispute_flagged":
    case "alert_refund_scheduled":
    case "alert_refund_cancelled":
    case "alert_refund_skipped":
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

function count(details: Activity["details"], key: string): string {
  const value = details[key];
  return typeof value === "number" ? String(value) : "";
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
    case "evidence_prepared":
      return `${evidenceTypeLabel(text(details, "evidence_type"))}, ${
        text(details, "status") === "approved"
          ? "approved to file"
          : "ready for review"
      }, ${count(details, "characters")} characters of notes`;
    case "dispute_escalated":
      return "From the inquiry to a claim, so PayPal takes the mandate record";
    case "evidence_filed":
      return `${evidenceTypeLabel(text(details, "evidence_type"))} in the ${STAGES[text(details, "stage")] ?? text(details, "stage")} stage${
        details["already_on_file"] === true ? ", found already on file" : ""
      }`;
    case "evidence_refused":
      return text(details, "issues");
    case "dispute_flagged":
      return text(details, "why");
    case "alert_refund_scheduled":
      return `A full refund at ${formatUtc(text(details, "due"))}, an hour before PayPal's window closes`;
    case "alert_refund_cancelled": {
      const outcome = text(details, "outcome");
      return `The alert closed first${outcome === "" ? "" : `: ${outcomeLabel(outcome)}`}; nothing was refunded`;
    }
    case "alert_refund_skipped":
      return text(details, "why");
    case "alert_refunded":
      return `Refund ${text(details, "refund_id")}, ${text(details, "refund_status").toLowerCase()}`;
    case "alert_refund_refused":
      return text(details, "issues");
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
