import type { ColDef } from "ag-grid-community";

import { AgGridReact, type CustomCellRendererProps } from "ag-grid-react";
import { Link } from "react-router";

import type { Activity } from "@/api/client";

import {
  ACTIVITY_LABELS,
  activityDetail,
  activitySource,
} from "@/activity-labels";
import { CopyButton } from "@/components/copy-button";
import { gridLayout, gridTheme } from "@/components/data-grid";
import { useMediaQuery } from "@/hooks/use-media-query";
import { formatUtc } from "@/money";

function OrderCell({ data }: CustomCellRendererProps<Activity>) {
  if (!data?.order_number)
    return <span className="text-muted-foreground">None</span>;
  return (
    <Link
      className="font-medium text-primary underline-offset-2 hover:underline"
      to={`/console/orders/${data.order_number}`}
    >
      {data.order_number}
    </Link>
  );
}

function MandateCell({ data }: CustomCellRendererProps<Activity>) {
  if (!data?.mandate_id)
    return <span className="text-muted-foreground">None</span>;
  return (
    <span className="flex h-full items-center gap-1">
      <span className="truncate font-mono text-xs">{data.mandate_id}</span>
      <CopyButton label="mandate ID" value={data.mandate_id} />
    </span>
  );
}

/** On a phone the time scrolls with the row, and who acted and the mandate wait for a wider screen. */
function columns(wide: boolean): ColDef<Activity>[] {
  return [
    {
      headerName: "At",
      field: "at",
      valueFormatter: ({ value }: { value: string | undefined }) =>
        value ? formatUtc(value) : "",
      sort: "desc",
      pinned: wide ? "left" : null,
      width: 230,
    },
    {
      headerName: "Who",
      valueGetter: ({ data }) => (data ? activitySource(data) : ""),
      hide: !wide,
      width: 170,
    },
    {
      headerName: "Event",
      field: "kind",
      valueFormatter: ({ value }: { value: Activity["kind"] | undefined }) =>
        value ? ACTIVITY_LABELS[value] : "",
      width: 240,
    },
    {
      headerName: "Detail",
      valueGetter: ({ data }) => (data ? activityDetail(data) : ""),
      tooltip: true,
      minWidth: 260,
      flex: 1,
    },
    {
      headerName: "Order",
      field: "order_number",
      cellRenderer: OrderCell,
      width: 130,
    },
    {
      headerName: "Mandate",
      field: "mandate_id",
      cellRenderer: MandateCell,
      hide: !wide,
      width: 330,
    },
  ];
}

/** `failed` keeps the "nothing yet" overlay off a grid that is empty because the request failed. */
export function ActivityGrid({
  rows,
  loading,
  failed,
}: {
  rows: Activity[];
  loading: boolean;
  failed: boolean;
}) {
  const wide = useMediaQuery("(min-width: 768px)");
  const layout = gridLayout(rows.length);

  return (
    <div className={layout.className}>
      <AgGridReact<Activity>
        columnDefs={columns(wide)}
        defaultColDef={{ resizable: true, sortable: true }}
        domLayout={layout.domLayout}
        getRowId={({ data }) => data.id}
        loading={loading}
        overlayLoadingTemplate="<span>Loading activity…</span>"
        overlayNoRowsTemplate="<span>No agent activity yet. It arrives here as agents shop.</span>"
        rowData={rows}
        suppressNoRowsOverlay={failed}
        theme={gridTheme}
      />
    </div>
  );
}
