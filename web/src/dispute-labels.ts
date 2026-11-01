import type { Dispute } from "@/api/client";

/** Whether `value` names one of `labels`' keys, for codes the API stores as plain text. */
function isKey<K extends string>(
  labels: Record<K, string>,
  value: string,
): value is K {
  return Object.hasOwn(labels, value);
}

/** The label for a code, or the code itself when it is one this build has no label for. */
function labelOf<K extends string>(
  labels: Record<K, string>,
  value: string,
): string {
  return isKey(labels, value) ? labels[value] : value;
}

/** Why the buyer disputed, in the words PayPal's Resolution Center uses. */
export const REASON_LABELS: Record<Dispute["reason"], string> = {
  MERCHANDISE_OR_SERVICE_NOT_RECEIVED: "Item not Received",
  MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED: "Not as Described",
  UNAUTHORISED: "Unauthorized",
  CREDIT_NOT_PROCESSED: "Credit not Processed",
  DUPLICATE_TRANSACTION: "Duplicate Transaction",
  INCORRECT_AMOUNT: "Incorrect Amount",
  PAYMENT_BY_OTHER_MEANS: "Paid by Other Means",
  CANCELED_RECURRING_BILLING: "Canceled Recurring Billing",
  PROBLEM_WITH_REMITTANCE: "Problem with Remittance",
  OTHER: "Other",
};

export const STATUS_LABELS: Record<Dispute["status"], string> = {
  OPEN: "Open",
  WAITING_FOR_BUYER_RESPONSE: "Waiting for Buyer",
  WAITING_FOR_SELLER_RESPONSE: "Waiting for Seller",
  UNDER_REVIEW: "PayPal Reviewing",
  RESOLVED: "Resolved",
  OTHER: "Other",
};

export const OUTCOME_LABELS: Record<NonNullable<Dispute["outcome"]>, string> = {
  RESOLVED_BUYER_FAVOUR: "Buyer Won",
  RESOLVED_SELLER_FAVOUR: "Seller Won",
  RESOLVED_WITH_PAYOUT: "PayPal Paid Out",
  CANCELED_BY_BUYER: "Canceled by Buyer",
  ACCEPTED: "Accepted",
  DENIED: "Denied",
  NONE: "Closed without a Decision",
};

export const CHANNEL_LABELS: Record<
  NonNullable<Dispute["dispute_channel"]>,
  string
> = {
  INTERNAL: "Filed with PayPal",
  EXTERNAL: "Filed with the Card Issuer",
  ALERT: "Pre-Chargeback Alert",
};

/** The response actions PayPal offers, named by their links; the sandbox adds two of its own. */
const ACTION_LABELS: Record<string, string> = {
  accept_claim: "Accept the Claim",
  acknowledge_return_item: "Acknowledge the Return",
  adjudicate: "Decide (Sandbox)",
  appeal: "Appeal",
  escalate: "Escalate to a Claim",
  make_offer: "Make an Offer",
  provide_evidence: "Provide Evidence",
  provide_supporting_info: "Provide Supporting Info",
  require_evidence: "Require Evidence (Sandbox)",
  send_message: "Send a Message",
};

/** A dispute in PayPal's claim stage is a PayPal claim, or a chargeback when the card issuer has it. */
export function stageLabel(
  stage: Dispute["stage"],
  channel: Dispute["dispute_channel"],
): string {
  switch (stage) {
    case "INQUIRY":
      return "Inquiry";
    case "CHARGEBACK":
      return channel === "INTERNAL" ? "Claim" : "Chargeback";
    case "PRE_ARBITRATION":
      return "Pre-Arbitration";
    case "ARBITRATION":
      return "Arbitration";
  }
}

export function actionLabel(rel: string): string {
  return ACTION_LABELS[rel] ?? rel;
}

export function reasonLabel(value: string): string {
  return labelOf(REASON_LABELS, value);
}

export function statusLabel(value: string): string {
  return labelOf(STATUS_LABELS, value);
}

export function outcomeLabel(value: string): string {
  return labelOf(OUTCOME_LABELS, value);
}
