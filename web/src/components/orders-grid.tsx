import type {
  CellKeyDownEvent,
  ColDef,
  RowClickedEvent,
} from "ag-grid-community";

import { AgGridReact, type CustomCellRendererProps } from "ag-grid-react";
import { Link, useNavigate } from "react-router";

import type { OrderSummary } from "@/api/client";

import { Channel } from "@/components/channel";
import { CopyButton } from "@/components/copy-button";
import { gridLayout, gridTheme } from "@/components/data-grid";
import { OrderStatus } from "@/components/order-status";
import { useMediaQuery } from "@/hooks/use-media-query";
import { formatAmount, formatUtc } from "@/money";

function orderPath(number: string): string {
  return `/console/orders/${number}`;
}

function NumberCell({ data }: CustomCellRendererProps<OrderSummary>) {
  return data ? (
    <Link
      className="font-medium text-primary underline-offset-2 hover:underline"
      to={orderPath(data.number)}
    >
      {data.number}
    </Link>
  ) : null;
}

function StatusCell({ data }: CustomCellRendererProps<OrderSummary>) {
  return data ? <OrderStatus status={data.status} /> : null;
}

function ChannelCell({ data }: CustomCellRendererProps<OrderSummary>) {
  return data ? <Channel channel={data.channel} /> : null;
}

function CaptureCell({ data }: CustomCellRendererProps<OrderSummary>) {
  if (!data?.capture_id)
    return <span className="text-muted-foreground">None</span>;
  return (
    <span className="flex h-full items-center gap-1">
      <span className="font-mono text-xs">{data.capture_id}</span>
      <CopyButton label="PayPal capture ID" value={data.capture_id} />
    </span>
  );
}

/** On a phone the order number scrolls with the row, and the buyer and capture wait for the order page. */
function columns(wide: boolean): ColDef<OrderSummary>[] {
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
      field: "created_at",
      valueFormatter: ({ value }: { value: string | undefined }) =>
        value ? formatUtc(value) : "",
      sort: "desc",
      width: 215,
    },
    {
      headerName: "Channel",
      field: "channel",
      cellRenderer: ChannelCell,
      width: 125,
    },
    {
      headerName: "Status",
      field: "status",
      cellRenderer: StatusCell,
      width: 155,
    },
    {
      headerName: "Total",
      field: "total.value",
      valueFormatter: ({ value }: { value: string | undefined }) =>
        value ? formatAmount(value) : "",
      comparator: (a: string, b: string) => Number(a) - Number(b),
      // A cellClass replaces the class the rightAligned type sets, so both are listed.
      type: "rightAligned",
      cellClass: ["ag-right-aligned-cell", "tabular-nums"],
      width: 115,
    },
    {
      headerName: "Buyer",
      field: "wallet_email",
      tooltip: true,
      hide: !wide,
      minWidth: 220,
      flex: 1,
    },
    {
      headerName: "PayPal Capture",
      field: "capture_id",
      cellRenderer: CaptureCell,
      hide: !wide,
      width: 225,
    },
  ];
}

/** `failed` keeps the "no orders yet" overlay off a grid that is empty because the request failed. */
export function OrdersGrid({
  orders,
  loading,
  failed,
}: {
  orders: OrderSummary[];
  loading: boolean;
  failed: boolean;
}) {
  const navigate = useNavigate();
  const wide = useMediaQuery("(min-width: 768px)");
  const layout = gridLayout(orders.length);

  const open = (event: RowClickedEvent<OrderSummary>) => {
    // A click on the number link or a copy button is handled by that control.
    const target = event.event?.target;
    if (target instanceof Element && target.closest("a, button")) return;
    if (event.data) void navigate(orderPath(event.data.number));
  };

  // Enter on any cell of a row opens its order, so the grid works from the keyboard.
  const openFromKeyboard = (event: CellKeyDownEvent<OrderSummary>) => {
    const key = event.event;
    if (key instanceof KeyboardEvent && key.key === "Enter" && event.data) {
      void navigate(orderPath(event.data.number));
    }
  };

  return (
    <div className={layout.className}>
      <AgGridReact<OrderSummary>
        columnDefs={columns(wide)}
        defaultColDef={{ resizable: true, sortable: true }}
        domLayout={layout.domLayout}
        getRowId={({ data }) => data.id}
        loading={loading}
        onCellKeyDown={openFromKeyboard}
        onRowClicked={open}
        overlayLoadingTemplate="<span>Loading orders…</span>"
        overlayNoRowsTemplate="<span>No orders yet. They appear here as buyers check out in the shop.</span>"
        rowClass="cursor-pointer"
        rowData={orders}
        suppressNoRowsOverlay={failed}
        theme={gridTheme}
      />
    </div>
  );
}
