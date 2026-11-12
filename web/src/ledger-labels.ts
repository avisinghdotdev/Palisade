import type { LedgerCheck, LedgerEntry, LedgerPayment } from "@/api/client";

import { formatAmount, formatUtc } from "@/money";
import { STATUS_LABELS } from "@/order-labels";

/** How each verdict of the ledger check reads to a merchant. */
export const VERDICT_LABELS: Record<LedgerEntry["verdict"], string> = {
  matched: "Matches",
  mismatch: "Differs",
  pending: "Not Listed Yet",
  missing: "Missing at PayPal",
};

/** PayPal's one-letter status of a ledger row, spelled out. */
const PAYPAL_STATUS: Record<string, string> = {
  S: "completed",
  P: "pending",
  D: "denied",
  V: "reversed",
};

/** An amount from PayPal's ledger: dollars as the console prints them, any other currency as PayPal states it. */
export function ledgerAmount(amount: LedgerPayment["amount"]): string {
  return amount.currency_code === "USD"
    ? formatAmount(amount.value)
    : `${amount.value} ${amount.currency_code}`;
}

function difference(
  entry: LedgerEntry,
  payment: LedgerPayment,
  field: LedgerEntry["differences"][number],
): string {
  switch (field) {
    case "amount":
      return `PayPal lists ${ledgerAmount(payment.amount)}, the order says ${formatAmount(entry.total.value)}.`;
    case "currency":
      return `PayPal lists the payment in ${payment.amount.currency_code}, the order is in ${entry.total.currency_code}.`;
    case "custom_id":
      if (payment.custom_id === null)
        return "PayPal holds no custom ID on the payment, the order has one.";
      return entry.custom_id === null
        ? "PayPal holds a custom ID on the payment, the order has none."
        : "PayPal holds a different custom ID on the payment.";
    case "refund":
      return entry.refunds.length > 0
        ? "PayPal lists a refund the order does not show."
        : "The order is refunded, and PayPal lists no refund.";
    case "status":
      return `PayPal lists the payment as ${PAYPAL_STATUS[payment.status] ?? payment.status}, the order is ${STATUS_LABELS[entry.status]}.`;
  }
}

/** What the check found on one order, in a sentence or two. */
export function ledgerNote(entry: LedgerEntry): string {
  const { payment } = entry;
  if (payment === null)
    return entry.verdict === "pending"
      ? "Too recent for PayPal's ledger, which lists a payment within three hours."
      : "PayPal's ledger reaches past this payment and does not list it.";
  if (entry.verdict === "pending")
    return "The order is refunded, and the refund is too recent for PayPal's ledger.";
  if (entry.verdict === "mismatch")
    return entry.differences
      .map((field) => difference(entry, payment, field))
      .join(" ");
  if (entry.refunds.length > 0)
    return "PayPal lists the same payment and its refund.";
  return entry.custom_id === null
    ? "PayPal lists the same amount and status."
    : "PayPal lists the same amount and status, with the mandate's ID and hash as the custom ID.";
}

/** The whole check in one line: how many orders, how they fared, and how fresh PayPal's ledger is. */
export function ledgerSummary(check: LedgerCheck): string {
  const { counts } = check;
  if (check.orders.length === 0) return "The shop has no paid order to check.";
  const strays = check.unclaimed.length;
  const tally = `${String(counts.matched)} match, ${String(counts.mismatch)} differ, ${String(counts.pending)} not listed yet, ${String(counts.missing)} missing.${
    strays === 0
      ? ""
      : ` PayPal also lists ${String(strays)} ${strays === 1 ? "payment" : "payments"} no paid order names.`
  }`;
  return check.ledger_as_of === null
    ? `PayPal's ledger does not reach the shop's first paid order yet. ${tally}`
    : `${String(check.orders.length)} paid orders checked against PayPal's ledger as of ${formatUtc(check.ledger_as_of)}: ${tally}`;
}
