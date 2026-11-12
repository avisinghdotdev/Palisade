import type {
  CellKeyDownEvent,
  ColDef,
  RowClickedEvent,
} from "ag-grid-community";

import { AgGridReact, type CustomCellRendererProps } from "ag-grid-react";
import { Link, useNavigate } from "react-router";

import type { LedgerEntry } from "@/api/client";

import { Channel } from "@/components/channel";
import { gridLayout, gridTheme } from "@/components/data-grid";
import { OrderStatus } from "@/components/order-status";
import { Badge } from "@/components/ui/badge";
import { useMediaQuery } from "@/hooks/use-media-query";
import { ledgerAmount, ledgerNote, VERDICT_LABELS } from "@/ledger-labels";
import { formatAmount, formatUtc } from "@/money";

const TONES: Record<LedgerEntry["verdict"], string> = {
  matched: "border-emerald-600/40 bg-emerald-50 text-emerald-800",
  mismatch: "border-destructive/40 bg-destructive/5 text-destructive",
  pending: "border-amber-600/40 bg-amber-50 text-amber-800",
  missing: "border-destructive/40 bg-destructive/5 text-destructive",
};

function orderPath(number: string): string {
  return `/console/orders/${number}`;
}

function NumberCell({ data }: CustomCellRendererProps<LedgerEntry>) {
  return data ? (
    <Link
      className="font-medium text-primary underline-offset-2 hover:underline"
      to={orderPath(data.number)}
    >
      {data.number}
    </Link>
  ) : null;
}

function ChannelCell({ data }: CustomCellRendererProps<LedgerEntry>) {
  return data ? <Channel channel={data.channel} /> : null;
}

function StatusCell({ data }: CustomCellRendererProps<LedgerEntry>) {
  return data ? <OrderStatus status={data.status} /> : null;
}

function VerdictCell({ data }: CustomCellRendererProps<LedgerEntry>) {
  return data ? (
    <Badge className={TONES[data.verdict]} variant="outline">
      {VERDICT_LABELS[data.verdict]}
    </Badge>
  ) : null;
}

/** On a phone the order number scrolls with the row, and who placed it and when wait for the order page. */
function columns(wide: boolean): ColDef<LedgerEntry>[] {
  return [
    {
      headerName: "Order",
      field: "number",
      cellRenderer: NumberCell,
      pinned: wide ? "left" : null,
      width: 140,
    },
    {
      headerName: "Placed",
      field: "placed_at",
      valueFormatter: ({ value }: { value: string | undefined }) =>
        value ? formatUtc(value) : "",
      sort: "desc",
      hide: !wide,
      width: 215,
    },
    {
      headerName: "Channel",
      field: "channel",
      cellRenderer: ChannelCell,
      hide: !wide,
      width: 125,
    },
    {
      headerName: "Order Status",
      field: "status",
      cellRenderer: StatusCell,
      width: 155,
    },
    {
      headerName: "Order Total",
      field: "total.value",
      valueFormatter: ({ value }: { value: string | undefined }) =>
        value ? formatAmount(value) : "",
      comparator: (a: string, b: string) => Number(a) - Number(b),
      // A cellClass replaces the class the rightAligned type sets, so both are listed.
      type: "rightAligned",
      cellClass: ["ag-right-aligned-cell", "tabular-nums"],
      width: 130,
    },
    {
      headerName: "PayPal Lists",
      valueGetter: ({ data }) =>
        data?.payment ? ledgerAmount(data.payment.amount) : "Not listed",
      type: "rightAligned",
      cellClass: ["ag-right-aligned-cell", "tabular-nums"],
      width: 135,
    },
    {
      headerName: "Check",
      field: "verdict",
      cellRenderer: VerdictCell,
      width: 160,
    },
    {
      headerName: "What the Check Found",
      valueGetter: ({ data }) => (data ? ledgerNote(data) : ""),
      wrapText: true,
      autoHeight: true,
      cellClass: "py-3.5 leading-5!",
      minWidth: 260,
      flex: 1,
    },
  ];
}

/** `failed` keeps the "no paid orders" overlay off a grid that is empty because the request failed. */
export function LedgerGrid({
  entries,
  loading,
  failed,
}: {
  entries: LedgerEntry[];
  loading: boolean;
  failed: boolean;
}) {
  const navigate = useNavigate();
  const wide = useMediaQuery("(min-width: 768px)");
  const layout = gridLayout(entries.length);

  const open = (event: RowClickedEvent<LedgerEntry>) => {
    // A click on the number link is handled by that link.
    const target = event.event?.target;
    if (target instanceof Element && target.closest("a, button")) return;
    if (event.data) void navigate(orderPath(event.data.number));
  };

  // Enter on any cell of a row opens its order, so the grid works from the keyboard.
  const openFromKeyboard = (event: CellKeyDownEvent<LedgerEntry>) => {
    const key = event.event;
    if (key instanceof KeyboardEvent && key.key === "Enter" && event.data) {
      void navigate(orderPath(event.data.number));
    }
  };

  return (
    <div className={layout.className}>
      <AgGridReact<LedgerEntry>
        columnDefs={columns(wide)}
        defaultColDef={{ resizable: true, sortable: true }}
        domLayout={layout.domLayout}
        getRowId={({ data }) => data.number}
        loading={loading}
        onCellKeyDown={openFromKeyboard}
        onRowClicked={open}
        overlayLoadingTemplate="<span>Reading PayPal's ledger…</span>"
        overlayNoRowsTemplate="<span>No paid orders yet. They appear here once PayPal has captured a payment.</span>"
        rowClass="cursor-pointer"
        rowData={entries}
        suppressNoRowsOverlay={failed}
        theme={gridTheme}
      />
    </div>
  );
}
