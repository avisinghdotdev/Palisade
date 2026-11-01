import type {
  CellKeyDownEvent,
  ColDef,
  RowClickedEvent,
} from "ag-grid-community";

import { AgGridReact, type CustomCellRendererProps } from "ag-grid-react";
import { Link, useNavigate } from "react-router";

import type { DisputeSummary } from "@/api/client";

import { Channel } from "@/components/channel";
import { gridLayout, gridTheme } from "@/components/data-grid";
import { DisputeStatus } from "@/components/dispute-status";
import { REASON_LABELS, stageLabel } from "@/dispute-labels";
import { useMediaQuery } from "@/hooks/use-media-query";
import { formatAmount, formatUtc } from "@/money";

function disputePath(id: string): string {
  return `/console/disputes/${id}`;
}

function DisputeCell({ data }: CustomCellRendererProps<DisputeSummary>) {
  return data ? (
    <Link
      className="font-mono text-xs font-medium text-primary underline-offset-2 hover:underline"
      to={disputePath(data.id)}
    >
      {data.id}
    </Link>
  ) : null;
}

function OrderCell({ data }: CustomCellRendererProps<DisputeSummary>) {
  if (!data) return null;
  return (
    <span className="flex h-full items-center gap-3">
      <Link
        className="font-medium text-primary underline-offset-2 hover:underline"
        to={`/console/orders/${data.order_number}`}
      >
        {data.order_number}
      </Link>
      <Channel channel={data.order_channel} />
    </span>
  );
}

function StatusCell({ data }: CustomCellRendererProps<DisputeSummary>) {
  return data ? (
    <DisputeStatus outcome={data.outcome} status={data.status} />
  ) : null;
}

function utc({ value }: { value: null | string | undefined }): string {
  return value ? formatUtc(value) : "";
}

/** On a phone the dispute id scrolls with the row, and the stage and deadline wait for the dispute page. */
function columns(wide: boolean): ColDef<DisputeSummary>[] {
  return [
    {
      headerName: "Dispute",
      field: "id",
      cellRenderer: DisputeCell,
      pinned: wide ? "left" : null,
      width: 175,
    },
    {
      headerName: "Opened",
      field: "opened_at",
      valueFormatter: utc,
      sort: "desc",
      width: 205,
    },
    {
      headerName: "Order",
      field: "order_number",
      cellRenderer: OrderCell,
      width: 190,
    },
    {
      headerName: "Reason",
      field: "reason",
      valueFormatter: ({
        value,
      }: {
        value: DisputeSummary["reason"] | undefined;
      }) => (value ? REASON_LABELS[value] : ""),
      width: 160,
    },
    {
      headerName: "Stage",
      valueGetter: ({ data }) =>
        data ? stageLabel(data.stage, data.dispute_channel) : "",
      hide: !wide,
      width: 105,
    },
    {
      headerName: "Status",
      field: "status",
      cellRenderer: StatusCell,
      width: 165,
    },
    {
      headerName: "Amount",
      field: "amount.value",
      valueFormatter: ({ value }: { value: string | undefined }) =>
        value ? formatAmount(value) : "",
      comparator: (a: string, b: string) => Number(a) - Number(b),
      // A cellClass replaces the class the rightAligned type sets, so both are listed.
      type: "rightAligned",
      cellClass: ["ag-right-aligned-cell", "tabular-nums"],
      width: 110,
    },
    {
      headerName: "Respond By",
      field: "seller_response_due_at",
      valueFormatter: utc,
      hide: !wide,
      minWidth: 205,
      flex: 1,
    },
  ];
}

/** `failed` keeps the "no disputes yet" overlay off a grid that is empty because the request failed. */
export function DisputesGrid({
  disputes,
  loading,
  failed,
}: {
  disputes: DisputeSummary[];
  loading: boolean;
  failed: boolean;
}) {
  const navigate = useNavigate();
  const wide = useMediaQuery("(min-width: 768px)");
  const layout = gridLayout(disputes.length);

  const open = (event: RowClickedEvent<DisputeSummary>) => {
    // A click on the dispute or order link is handled by that link.
    const target = event.event?.target;
    if (target instanceof Element && target.closest("a, button")) return;
    if (event.data) void navigate(disputePath(event.data.id));
  };

  // Enter on any cell of a row opens its dispute, so the grid works from the keyboard.
  const openFromKeyboard = (event: CellKeyDownEvent<DisputeSummary>) => {
    const key = event.event;
    if (key instanceof KeyboardEvent && key.key === "Enter" && event.data) {
      void navigate(disputePath(event.data.id));
    }
  };

  return (
    <div className={layout.className}>
      <AgGridReact<DisputeSummary>
        columnDefs={columns(wide)}
        defaultColDef={{ resizable: true, sortable: true }}
        domLayout={layout.domLayout}
        getRowId={({ data }) => data.id}
        loading={loading}
        onCellKeyDown={openFromKeyboard}
        onRowClicked={open}
        overlayLoadingTemplate="<span>Loading disputes…</span>"
        overlayNoRowsTemplate="<span>No disputes yet. They appear here when a buyer disputes a payment.</span>"
        rowClass="cursor-pointer"
        rowData={disputes}
        suppressNoRowsOverlay={failed}
        theme={gridTheme}
      />
    </div>
  );
}
