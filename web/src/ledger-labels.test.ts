import { describe, expect, test } from "vitest";

import type { LedgerCheck, LedgerEntry } from "@/api/client";

import { ledgerNote, ledgerSummary } from "@/ledger-labels";

const CUSTOM = "01a18dd9-6eed-7552-9dd2-eab4a9ba8ca7:7c07ea3f";

function entry(changes: Partial<LedgerEntry>): LedgerEntry {
  return {
    number: "RR-TEST01",
    channel: "agent",
    status: "captured",
    total: { value: "120.00", currency_code: "USD" },
    capture_id: "CAP-1",
    custom_id: CUSTOM,
    placed_at: "2026-11-11T08:00:00Z",
    verdict: "matched",
    differences: [],
    payment: {
      event_code: "T0003",
      status: "S",
      amount: { value: "120.00", currency_code: "USD" },
      custom_id: CUSTOM,
      at: "2026-11-11T08:00:06Z",
    },
    refunds: [],
    ...changes,
  };
}

describe("ledgerNote", () => {
  test("says what agrees on a match", () => {
    expect(ledgerNote(entry({}))).toBe(
      "PayPal lists the same amount and status, with the mandate's ID and hash as the custom ID.",
    );
    expect(ledgerNote(entry({ custom_id: null }))).toBe(
      "PayPal lists the same amount and status.",
    );
  });

  test("names every field that differs, with both sides", () => {
    const differing = entry({
      verdict: "mismatch",
      differences: ["amount", "custom_id", "status", "refund"],
      payment: {
        event_code: "T0003",
        status: "V",
        amount: { value: "119.00", currency_code: "USD" },
        custom_id: null,
        at: "2026-11-11T08:00:06Z",
      },
      refunds: [
        {
          id: "REFUND-1",
          status: "S",
          amount: { value: "-119.00", currency_code: "USD" },
          at: "2026-11-11T09:00:00Z",
        },
      ],
    });

    expect(ledgerNote(differing)).toBe(
      "PayPal lists $119.00, the order says $120.00." +
        " PayPal holds no custom ID on the payment, the order has one." +
        " PayPal lists the payment as reversed, the order is Paid." +
        " PayPal lists a refund the order does not show.",
    );
  });

  test("tells a payment PayPal has yet to list from one it should have", () => {
    expect(ledgerNote(entry({ verdict: "pending", payment: null }))).toContain(
      "within three hours",
    );
    expect(ledgerNote(entry({ verdict: "missing", payment: null }))).toContain(
      "does not list it",
    );
  });
});

describe("ledgerSummary", () => {
  const check: LedgerCheck = {
    checked_at: "2026-11-11T21:31:38Z",
    ledger_as_of: "2026-11-11T19:29:59Z",
    counts: { matched: 1, mismatch: 0, pending: 1, missing: 0 },
    orders: [entry({}), entry({ verdict: "pending", payment: null })],
    unclaimed: [],
  };

  test("counts the verdicts and dates PayPal's ledger", () => {
    expect(ledgerSummary(check)).toBe(
      "2 paid orders checked against PayPal's ledger as of 11 Nov 2026, 19:29:59 UTC:" +
        " 1 match, 0 differ, 1 not listed yet, 0 missing.",
    );
  });

  test("counts the payments no paid order names", () => {
    const stray = {
      id: "7YS35007E3491073E",
      status: "S",
      amount: { value: "80.00", currency_code: "USD" },
      invoice_id: "RR-4T96GA",
      custom_id: null,
      at: "2026-10-28T11:40:00Z",
    };

    expect(ledgerSummary({ ...check, unclaimed: [stray] })).toContain(
      "PayPal also lists 1 payment no paid order names.",
    );
    expect(ledgerSummary({ ...check, unclaimed: [stray, stray] })).toContain(
      "2 payments no paid order names.",
    );
  });

  test("says so when PayPal's ledger does not reach the first order", () => {
    expect(ledgerSummary({ ...check, ledger_as_of: null })).toContain(
      "does not reach the shop's first paid order yet",
    );
  });
});
