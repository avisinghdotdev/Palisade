import type {
  AgDataSourcesDefinition,
  AgFieldDefinition,
  AgReportState,
  AgResponsiveLayoutBreakpoint,
  AgSortConfig,
  AgTitle,
  AgWidgetLayoutState,
} from "ag-studio";

import type { AnalyticsRows, DisputeRow, OrderRow } from "@/api/client";

import { reasonLabel, statusLabel } from "@/dispute-labels";
import { STATUS_LABELS as PAYMENT_LABELS } from "@/order-labels";

const CHANNEL_LABELS: Record<OrderRow["channel"], string> = {
  agent: "Agent",
  human: "Person",
};

const RESULT_LABELS: Record<DisputeRow["result"], string> = {
  open: "Open",
  won: "Won",
  lost: "Lost",
  closed: "Closed, No Winner",
};

const EVIDENCE_LABELS: Record<DisputeRow["evidence"], string> = {
  none: "Not Prepared",
  prepared: "Awaiting Review",
  approved: "Approved",
  filed: "Filed",
  refused: "Refused by PayPal",
};

// The console's own money and date styles. Studio reads a plain date as midnight in the
// browser's time zone, so the day is formatted in that zone, not in UTC.
const USD = {
  format: new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }),
};
const DAY = {
  format: new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }),
};
const ONE_DECIMAL = { format: "0.0" };
// A value on each bar: a rate or an hour count is read off the bar, not the axis.
const LABELLED_BARS = {
  theme: { bar: { series: { label: { enabled: true } } } },
};

const ORDER_FIELDS: AgFieldDefinition[] = [
  { id: "order_id", name: "Order ID", format: "textFormat", hide: true },
  { id: "number", name: "Order", format: "textFormat" },
  { id: "placed_on", name: "Placed", format: "dateFormat", formatOptions: DAY },
  {
    id: "channel",
    name: "Buyer",
    format: "textFormat",
    description:
      "Agent when a shopping agent paid under the buyer's signed mandate, Person when the buyer checked out.",
  },
  { id: "agent", name: "Agent", format: "textFormat", blankValue: "None" },
  { id: "status", name: "Payment", format: "textFormat" },
  {
    id: "amount",
    name: "Order Amount",
    format: "currencyFormat",
    formatOptions: USD,
  },
  {
    id: "dispute_rate",
    name: "Dispute Rate",
    format: "percentageFormat",
    description:
      "1 for a paid order the buyer disputed, 0 for one they did not; averaged, it is the dispute rate.",
  },
];

const DISPUTE_FIELDS: AgFieldDefinition[] = [
  { id: "dispute_id", name: "Dispute", format: "textFormat" },
  { id: "order_id", name: "Order ID", format: "textFormat", hide: true },
  { id: "opened_on", name: "Opened", format: "dateFormat", formatOptions: DAY },
  { id: "channel", name: "Buyer", format: "textFormat" },
  { id: "reason", name: "Reason", format: "textFormat" },
  { id: "status", name: "Status", format: "textFormat" },
  { id: "result", name: "Result", format: "textFormat" },
  { id: "evidence", name: "Evidence", format: "textFormat" },
  {
    id: "win_rate",
    name: "Win Rate",
    format: "percentageFormat",
    description:
      "1 for a dispute PayPal decided for the store, 0 for one it decided against, blank until it decides; averaged, it is the win rate.",
  },
  {
    id: "minutes_to_evidence",
    name: "Minutes to Evidence",
    format: "decimalFormat",
    formatOptions: ONE_DECIMAL,
    blankValue: "",
    description:
      "From PayPal opening the dispute to Palisade's first answer PayPal took.",
  },
  {
    id: "hours_to_evidence",
    name: "Hours to Evidence",
    format: "decimalFormat",
    formatOptions: ONE_DECIMAL,
    blankValue: "",
    description: "Minutes to evidence, in hours.",
  },
  {
    id: "amount",
    name: "Disputed Amount",
    format: "currencyFormat",
    formatOptions: USD,
  },
];

/** Won 1, lost 0, and no value while PayPal has not decided, so the average is the win rate. */
function winRate(result: DisputeRow["result"]): number | null {
  switch (result) {
    case "won":
      return 1;
    case "lost":
      return 0;
    case "open":
    case "closed":
      return null;
  }
}

function dollars(cents: number): number {
  return cents / 100;
}

/** The API's rows as the dashboard's two tables, labelled the way the rest of the console reads. */
export function analyticsSources(rows: AnalyticsRows): AgDataSourcesDefinition {
  return {
    sources: [
      {
        id: "orders",
        name: "Paid Orders",
        primaryKeyField: "order_id",
        fields: ORDER_FIELDS,
        data: rows.orders.map((order) => ({
          order_id: order.order_id,
          number: order.number,
          placed_on: order.placed_on,
          channel: CHANNEL_LABELS[order.channel],
          agent: order.agent_name,
          status: PAYMENT_LABELS[order.status],
          amount: dollars(order.amount_cents),
          dispute_rate: order.disputed ? 1 : 0,
        })),
      },
      {
        id: "disputes",
        name: "Disputes",
        primaryKeyField: "dispute_id",
        fields: DISPUTE_FIELDS,
        data: rows.disputes.map((dispute) => {
          const minutes = dispute.minutes_to_evidence ?? null;
          return {
            dispute_id: dispute.dispute_id,
            order_id: dispute.order_id,
            opened_on: dispute.opened_on,
            channel: CHANNEL_LABELS[dispute.channel],
            reason: reasonLabel(dispute.reason),
            status: statusLabel(dispute.status),
            result: RESULT_LABELS[dispute.result],
            evidence: EVIDENCE_LABELS[dispute.evidence],
            win_rate: winRate(dispute.result),
            minutes_to_evidence: minutes,
            hours_to_evidence: minutes === null ? null : minutes / 60,
            amount: dollars(dispute.amount_cents),
          };
        }),
      },
    ],
    relationships: [
      {
        id: "dispute-order",
        type: "many-to-one",
        source: "disputes.order_id",
        target: "orders.order_id",
      },
    ],
  };
}

function title(text: string): AgTitle {
  return { enabled: true, text };
}

// "Agent" sorts before "Person" on every chart, so each takes the same palette colour everywhere.
const AGENT_FIRST = (table: "orders" | "disputes"): AgSortConfig[] => [
  { field: { id: `${table}.channel` }, direction: "asc" },
];

/** The dashboard as the merchant opens it: the four numbers, how they split, and every dispute. */
export const ANALYTICS_REPORT: AgReportState = {
  selectedPageId: "overview",
  pages: [
    {
      id: "overview",
      widgets: {
        "paid-orders": {
          type: "value",
          format: {
            title: title("Paid Orders"),
            caption: title("Orders whose payment PayPal took"),
          },
          dataMapping: {
            value: [{ id: "orders.number", aggregation: "count" }],
          },
        },
        "dispute-rate": {
          type: "value",
          format: {
            title: title("Dispute Rate"),
            caption: title("Paid orders the buyer disputed"),
          },
          dataMapping: {
            value: [{ id: "orders.dispute_rate", aggregation: "avg" }],
          },
        },
        "win-rate": {
          type: "value",
          format: {
            title: title("Win Rate"),
            caption: title("Of the disputes PayPal has decided"),
          },
          dataMapping: {
            value: [{ id: "disputes.win_rate", aggregation: "avg" }],
          },
        },
        "time-to-evidence": {
          type: "value",
          format: {
            title: title("Hours to Evidence"),
            caption: title("Average, from dispute opened to evidence filed"),
          },
          dataMapping: {
            value: [{ id: "disputes.hours_to_evidence", aggregation: "avg" }],
          },
        },
        // Each buyer is a series of its own, so its bar takes its palette colour; stacked, the
        // one bar a buyer has sits in the middle of its category.
        "dispute-rate-by-buyer": {
          type: "column-chart-stacked",
          format: {
            title: title("Dispute Rate by Buyer"),
            style: LABELLED_BARS,
          },
          dataMapping: {
            categoryKey: [{ id: "orders.channel" }],
            valueKey: [{ id: "orders.dispute_rate", aggregation: "avg" }],
            legendKey: [{ id: "orders.channel" }],
          },
          sort: AGENT_FIRST("orders"),
        },
        "orders-by-buyer": {
          type: "donut-chart",
          format: { title: title("Paid Orders by Buyer") },
          dataMapping: {
            categoryKey: [{ id: "orders.channel" }],
            valueKey: [{ id: "orders.number", aggregation: "count" }],
          },
          sort: AGENT_FIRST("orders"),
        },
        "dispute-results": {
          type: "donut-chart",
          format: { title: title("Dispute Results") },
          dataMapping: {
            categoryKey: [{ id: "disputes.result" }],
            valueKey: [{ id: "disputes.dispute_id", aggregation: "count" }],
          },
        },
        "disputes-by-reason": {
          type: "bar-chart-grouped",
          format: {
            title: title("Disputes by Reason"),
            style: { theme: { common: { legend: { enabled: true } } } },
          },
          dataMapping: {
            categoryKey: [{ id: "disputes.reason" }],
            valueKey: [{ id: "disputes.dispute_id", aggregation: "count" }],
            legendKey: [{ id: "disputes.channel" }],
          },
          sort: AGENT_FIRST("disputes"),
        },
        "evidence-by-day": {
          type: "column-chart-grouped",
          format: {
            title: title("Hours to Evidence by Day Opened"),
            style: LABELLED_BARS,
          },
          dataMapping: {
            categoryKey: [{ id: "disputes.opened_on" }],
            valueKey: [
              { id: "disputes.hours_to_evidence", aggregation: "avg" },
            ],
          },
        },
        disputes: {
          type: "grid",
          format: {
            title: title("Every Dispute"),
            style: { columnAutoSize: true },
          },
          dataMapping: {
            cols: [
              { id: "disputes.opened_on" },
              { id: "disputes.dispute_id" },
              { id: "orders.number" },
              { id: "disputes.channel" },
              { id: "disputes.reason" },
              { id: "disputes.evidence" },
              { id: "disputes.result" },
              { id: "disputes.minutes_to_evidence" },
              { id: "disputes.amount" },
            ],
          },
          sort: [{ field: { id: "disputes.opened_on" }, direction: "desc" }],
        },
      },
      // 24 columns of 16-pixel rows, Studio's own grid.
      widgetLayout: {
        "paid-orders": { xTrack: 0, yTrack: 0, xSpan: 6, ySpan: 8 },
        "dispute-rate": { xTrack: 6, yTrack: 0, xSpan: 6, ySpan: 8 },
        "win-rate": { xTrack: 12, yTrack: 0, xSpan: 6, ySpan: 8 },
        "time-to-evidence": { xTrack: 18, yTrack: 0, xSpan: 6, ySpan: 8 },
        "dispute-rate-by-buyer": { xTrack: 0, yTrack: 8, xSpan: 8, ySpan: 18 },
        "orders-by-buyer": { xTrack: 8, yTrack: 8, xSpan: 8, ySpan: 18 },
        "dispute-results": { xTrack: 16, yTrack: 8, xSpan: 8, ySpan: 18 },
        "disputes-by-reason": { xTrack: 0, yTrack: 26, xSpan: 12, ySpan: 18 },
        "evidence-by-day": { xTrack: 12, yTrack: 26, xSpan: 12, ySpan: 18 },
        disputes: { xTrack: 0, yTrack: 44, xSpan: 24, ySpan: 22 },
      },
    },
  ],
};

/** One widget per row, top to bottom and left to right as the wide layout reads. */
export function stacked(
  layouts: readonly AgWidgetLayoutState[],
  columns: number,
): AgWidgetLayoutState[] {
  const reading = layouts
    .map((layout, index) => ({ layout, index }))
    .toSorted(
      (a, b) =>
        a.layout.yTrack - b.layout.yTrack || a.layout.xTrack - b.layout.xTrack,
    );
  const placed: AgWidgetLayoutState[] = [];
  let yTrack = 0;
  for (const { layout, index } of reading) {
    placed[index] = { xTrack: 0, yTrack, xSpan: columns, ySpan: layout.ySpan };
    yTrack += layout.ySpan;
  }
  return placed;
}

/** The layout on a narrow screen, at any canvas width: the page picks it and sizes for it. */
export const STACKED_LAYOUT: AgResponsiveLayoutBreakpoint[] = [
  {
    layout: (candidates, { columns }) =>
      stacked(
        candidates.map((candidate) => candidate.layout),
        columns,
      ),
  },
];
