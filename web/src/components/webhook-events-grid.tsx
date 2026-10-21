import {
  AllCommunityModule,
  type ColDef,
  ModuleRegistry,
  themeQuartz,
  type ValueFormatterParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";

import type { ReceivedEvent } from "@/api/client";

ModuleRegistry.registerModules([AllCommunityModule]);

// The grid reads the surrounding theme's variables, so it matches the console it sits in.
const theme = themeQuartz.withParams({
  accentColor: "var(--primary)",
  backgroundColor: "var(--card)",
  foregroundColor: "var(--foreground)",
  headerBackgroundColor: "var(--secondary)",
  borderColor: "var(--border)",
  fontFamily: "inherit",
  headerFontWeight: 600,
  borderRadius: 8,
  wrapperBorderRadius: 12,
});

const utcTime = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "medium",
  timeZone: "UTC",
});

function formatTime({
  value,
}: ValueFormatterParams<ReceivedEvent, null | string>): string {
  return value ? `${utcTime.format(new Date(value))} UTC` : "not yet";
}

const columns: ColDef<ReceivedEvent>[] = [
  {
    headerName: "Received",
    field: "received_at",
    valueFormatter: formatTime,
    sort: "desc",
    width: 230,
  },
  { headerName: "Event", field: "event.event_type", flex: 1, minWidth: 240 },
  { headerName: "Summary", field: "event.summary", flex: 2, minWidth: 260 },
  {
    headerName: "Processed",
    field: "processed_at",
    valueFormatter: formatTime,
    width: 230,
  },
  { headerName: "Event id", field: "event.id", width: 340 },
];

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
  return (
    <div className="h-[32rem]">
      <AgGridReact<ReceivedEvent>
        columnDefs={columns}
        defaultColDef={{ resizable: true, sortable: true }}
        loading={loading}
        overlayLoadingTemplate="<span>Loading events…</span>"
        overlayNoRowsTemplate="<span>No PayPal events yet. They arrive here as PayPal sends them.</span>"
        rowData={events}
        suppressNoRowsOverlay={failed}
        theme={theme}
      />
    </div>
  );
}
