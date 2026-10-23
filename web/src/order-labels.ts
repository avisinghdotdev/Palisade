import type { Order } from "@/api/client";

/** How each order status reads to a merchant. */
export const STATUS_LABELS: Record<Order["status"], string> = {
  creating: "Creating",
  declined: "Declined",
  failed: "Not Confirmed",
  authorized: "Authorized",
  capture_pending: "Payment Pending",
  captured: "Paid",
  voided: "Voided",
  refunded: "Refunded",
  reversed: "Reversed",
};

/** What happened when an order reached each status, as its timeline tells it. */
export const EVENT_LABELS: Record<Order["status"], string> = {
  creating: "Order Created",
  declined: "Payment Declined",
  failed: "No Answer from PayPal",
  authorized: "Payment Authorized",
  capture_pending: "Capture Pending",
  captured: "Payment Captured",
  voided: "Authorization Voided",
  refunded: "Payment Refunded",
  reversed: "Payment Reversed",
};
