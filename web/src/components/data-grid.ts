import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
} from "ag-grid-community";

ModuleRegistry.registerModules([AllCommunityModule]);

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
