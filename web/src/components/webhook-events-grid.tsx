import type { ColDef } from "ag-grid-community";

import { AgGridReact, type CustomCellRendererProps } from "ag-grid-react";

import type { ReceivedEvent } from "@/api/client";

import { CopyButton } from "@/components/copy-button";
import { gridLayout, gridTheme } from "@/components/data-grid";
import { useMediaQuery } from "@/hooks/use-media-query";
import { formatUtc } from "@/money";

function EventIdCell({ data }: CustomCellRendererProps<ReceivedEvent>) {
  return data ? (
    <span className="flex h-full items-center gap-1">
      <span className="truncate font-mono text-xs">{data.event.id}</span>
      <CopyButton label="PayPal event ID" value={data.event.id} />
    </span>
  ) : null;
}

/** On a phone nothing is pinned, and the processing time and event ID wait for a wider screen. */
function columns(wide: boolean): ColDef<ReceivedEvent>[] {
  return [
    {
      headerName: "Received",
      field: "received_at",
      valueFormatter: ({ value }: { value: string | undefined }) =>
        value ? formatUtc(value) : "",
      sort: "desc",
      pinned: wide ? "left" : null,
      width: 230,
    },
    {
      headerName: "Event",
      field: "event.event_type",
      cellClass: "font-mono text-xs",
      width: 300,
    },
    {
      headerName: "Summary",
      field: "event.summary",
      tooltip: true,
      minWidth: 240,
      flex: 1,
    },
    {
      headerName: "Processed",
      field: "processed_at",
      valueFormatter: ({ value }: { value: null | string | undefined }) =>
        value ? formatUtc(value) : "Waiting",
      hide: !wide,
      width: 230,
    },
    {
      headerName: "Event ID",
      field: "event.id",
      cellRenderer: EventIdCell,
      hide: !wide,
      width: 310,
    },
  ];
}

/** `failed` keeps the "no events yet" overlay off a grid that is empty because the request failed. */
export function WebhookEventsGrid({
  events,
  loading,
  failed,
}: {
  events: ReceivedEvent[];
  loading: boolean;
  failed: boolean;
}) {
  const wide = useMediaQuery("(min-width: 768px)");
  const layout = gridLayout(events.length);

  return (
    <div className={layout.className}>
      <AgGridReact<ReceivedEvent>
        columnDefs={columns(wide)}
        defaultColDef={{ resizable: true, sortable: true }}
        domLayout={layout.domLayout}
        getRowId={({ data }) => data.event.id}
        loading={loading}
        overlayLoadingTemplate="<span>Loading events…</span>"
        overlayNoRowsTemplate="<span>No PayPal events yet. They arrive here as PayPal sends them.</span>"
        rowData={events}
        suppressNoRowsOverlay={failed}
        theme={gridTheme}
      />
    </div>
  );
}
