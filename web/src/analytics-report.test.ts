import { describe, expect, it } from "vitest";

import type { AnalyticsRows } from "@/api/client";

import {
  ANALYTICS_REPORT,
  analyticsSources,
  stacked,
} from "@/analytics-report";

const ROWS: AnalyticsRows = {
  license_key: "KEY",
  orders: [
    {
      order_id: "0190a000-0000-7000-8000-000000000001",
      number: "RR-AGENT",
      placed_on: "2026-11-02",
      channel: "agent",
      agent_name: "Ridgeline Assistant",
      status: "captured",
      amount_cents: 12000,
      disputed: true,
    },
    {
      order_id: "0190a000-0000-7000-8000-000000000002",
      number: "RR-HUMAN",
      placed_on: "2026-11-03",
      channel: "human",
      agent_name: null,
      status: "refunded",
      amount_cents: 9550,
      disputed: false,
    },
  ],
  disputes: [
    {
      dispute_id: "PP-R-AAA-00000001",
      order_id: "0190a000-0000-7000-8000-000000000001",
      opened_on: "2026-11-02",
      channel: "agent",
      reason: "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
      status: "RESOLVED",
      result: "won",
      evidence: "filed",
      minutes_to_evidence: 4.5,
      amount_cents: 12000,
    },
    {
      dispute_id: "PP-R-AAA-00000002",
      order_id: "0190a000-0000-7000-8000-000000000001",
      opened_on: "2026-11-03",
      channel: "agent",
      reason: "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
      status: "UNDER_REVIEW",
      result: "open",
      evidence: "none",
      minutes_to_evidence: null,
      amount_cents: 12000,
    },
  ],
};

/** Each table's rows and fields by its id. */
function tablesOf(rows: AnalyticsRows) {
  return new Map(
    analyticsSources(rows).sources.map((source) => [
      source.id,
      "data" in source && "fields" in source
        ? { data: source.data, fields: source.fields ?? [] }
        : { data: [], fields: [] },
    ]),
  );
}

describe("the analytics dashboard", () => {
  it("names only visible fields its tables declare", () => {
    const tables = tablesOf(ROWS);
    // A field reference is the only id in the report written as table.field.
    const references = [
      ...JSON.stringify(ANALYTICS_REPORT).matchAll(
        /"id":"([a-z_]+)\.([a-z_]+)"/g,
      ),
    ].map(([reference = "", table = "", field = ""]) => ({
      reference,
      declared: tables.get(table)?.fields.find(({ id }) => id === field),
    }));

    expect(references.length).toBeGreaterThan(10);
    expect(
      references
        .filter(({ declared }) => declared === undefined || declared.hide)
        .map(({ reference }) => reference),
    ).toEqual([]);
  });

  it("lays out every widget it defines", () => {
    const page = ANALYTICS_REPORT.pages[0];

    expect(Object.keys(page?.widgetLayout ?? {}).toSorted()).toEqual(
      Object.keys(page?.widgets ?? {}).toSorted(),
    );
  });

  it("labels the rows the way the console does and turns rates into averages", () => {
    const tables = tablesOf(ROWS);

    expect(tables.get("orders")?.data).toEqual([
      expect.objectContaining({
        channel: "Agent",
        agent: "Ridgeline Assistant",
        status: "Paid",
        amount: 120,
        dispute_rate: 1,
      }),
      expect.objectContaining({
        channel: "Person",
        agent: null,
        status: "Refunded",
        amount: 95.5,
        dispute_rate: 0,
      }),
    ]);
    expect(tables.get("disputes")?.data).toEqual([
      expect.objectContaining({
        reason: "Item not Received",
        status: "Resolved",
        result: "Won",
        evidence: "Filed",
        win_rate: 1,
        minutes_to_evidence: 4.5,
      }),
      expect.objectContaining({
        result: "Open",
        evidence: "Not Prepared",
        win_rate: null,
        minutes_to_evidence: null,
      }),
    ]);
  });

  it("stacks the widgets in reading order on a narrow canvas", () => {
    const placed = stacked(
      [
        { xTrack: 12, yTrack: 0, xSpan: 12, ySpan: 7 },
        { xTrack: 0, yTrack: 7, xSpan: 24, ySpan: 18 },
        { xTrack: 0, yTrack: 0, xSpan: 12, ySpan: 7 },
      ],
      24,
    );

    expect(placed).toEqual([
      { xTrack: 0, yTrack: 7, xSpan: 24, ySpan: 7 },
      { xTrack: 0, yTrack: 14, xSpan: 24, ySpan: 18 },
      { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 7 },
    ]);
  });
});
