import {
  CellStyleModule,
  ClientSideRowModelModule,
  ModuleRegistry,
  RowAutoHeightModule,
  RowStyleModule,
  themeQuartz,
  TooltipModule,
  ValidationModule,
} from "ag-grid-community";

// Only the features the console's grids use beyond AG Grid's core, which keeps the rest out of
// the bundle. The validation module names a missing one in the browser console, so it loads in
// development only.
ModuleRegistry.registerModules([
  CellStyleModule,
  ClientSideRowModelModule,
  RowAutoHeightModule,
  RowStyleModule,
  TooltipModule,
  ...(import.meta.env.DEV ? [ValidationModule] : []),
]);

/** The console's grids read the surrounding theme's variables, so they match the page they sit in. */
export const gridTheme = themeQuartz.withParams({
  accentColor: "var(--primary)",
  backgroundColor: "var(--card)",
  foregroundColor: "var(--foreground)",
  headerBackgroundColor: "var(--secondary)",
  headerTextColor: "var(--muted-foreground)",
  rowHoverColor: "var(--accent)",
  borderColor: "var(--border)",
  columnBorder: false,
  fontFamily: "inherit",
  fontSize: 14,
  headerFontSize: 13,
  headerFontWeight: 500,
  rowHeight: 48,
  headerHeight: 42,
  cellHorizontalPadding: 16,
  borderRadius: 8,
  wrapperBorderRadius: 12,
});

const ROWS_BEFORE_SCROLLING = 10;

/** A short grid is as tall as its rows; a long one fills the window below the heading and scrolls inside. */
export function gridLayout(rows: number) {
  return rows > ROWS_BEFORE_SCROLLING
    ? {
        domLayout: "normal" as const,
        className: "h-[calc(100dvh-16rem)] min-h-96",
      }
    : { domLayout: "autoHeight" as const, className: "" };
}
