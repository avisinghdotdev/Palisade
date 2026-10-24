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

type PaymentEvent = NonNullable<Order["events"][number]["paypal_event"]>;

/** What each PayPal payment webhook reported, as the order's timeline tells it. */
export const WEBHOOK_LABELS: Record<PaymentEvent, string> = {
  "PAYMENT.AUTHORIZATION.CREATED": "PayPal Confirmed the Authorization",
  "PAYMENT.AUTHORIZATION.VOIDED": "PayPal Confirmed the Void",
  "PAYMENT.CAPTURE.PENDING": "PayPal Reported the Capture Pending",
  "PAYMENT.CAPTURE.COMPLETED": "PayPal Confirmed the Capture",
  "PAYMENT.CAPTURE.DECLINED": "PayPal Declined the Capture",
  "PAYMENT.CAPTURE.REFUNDED": "PayPal Refunded the Capture",
  "PAYMENT.CAPTURE.REVERSED": "PayPal Reversed the Capture",
};

/** What happened when the checkout moved an order to each status, as its timeline tells it. */
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
