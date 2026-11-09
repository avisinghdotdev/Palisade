import {
  type AgPanelConfig,
  type AgResponsiveLayoutBreakpoint,
  AgStudioAiModule,
  AgStudioModuleRegistry,
  enableStudioDevValidations,
  studioTheme,
} from "ag-studio";
import { AgStudio, AgStudioProvider } from "ag-studio-react";
import { Check, Pencil } from "lucide-react";
import { useMemo, useState } from "react";

import type { AnalyticsRows } from "@/api/client";

import { analyticsAi } from "@/analytics-agent";
import {
  ANALYTICS_REPORT,
  analyticsSources,
  STACKED_LAYOUT,
} from "@/analytics-report";
import { AssistantAccess } from "@/components/assistant-access";
import { Button } from "@/components/ui/button";
import { useMediaQuery } from "@/hooks/use-media-query";
import { useShopper } from "@/shopper";

// Studio shows its agents' chat panel only with its AI module registered.
AgStudioModuleRegistry.registerModules([AgStudioAiModule]);

// Studio names a broken widget or field reference in the browser console; development only.
if (import.meta.env.DEV) {
  enableStudioDevValidations();
}

// The console's own colours, so the dashboard reads as part of the page. Charts draw on a
// canvas, which cannot read CSS variables, so their colours are the theme's values in hex.
const theme = studioTheme.withParams({
  accentColor: "var(--primary)",
  backgroundColor: "var(--card)",
  foregroundColor: "var(--foreground)",
  borderColor: "var(--border)",
  fontFamily: "inherit",
  fontSize: 14,
  borderRadius: 8,
  // Studio's canvas keeps 720 pixels by default; the stacked layout serves narrow screens instead.
  studioCanvasMinWidth: 0,
  studioWrapperBorder: false,
  studioWrapperBackgroundColor: "var(--background)",
  studioCanvasBackgroundColor: "var(--background)",
  studioWidgetBackgroundColor: "var(--card)",
  studioWidgetBorder: { color: "var(--border)" },
  studioWidgetBorderRadius: 12,
  studioWidgetTitleFontSize: 15,
  studioWidgetTitleFontWeight: 600,
  studioValueWidgetFontSize: 32,
  chartFontFamily: "Geist Variable, sans-serif",
  chartTextColor: "#3b2f29",
  chartSubtleTextColor: "#7a6a60",
  chartPaletteFills1Color: "#4b44b5",
  chartPaletteFills2Color: "#b4461c",
  chartPaletteFills3Color: "#0f7a5a",
  chartPaletteFills4Color: "#a39a92",
  chartPaletteFills5Color: "#c98a1a",
  chartPaletteFills6Color: "#2f7fa8",
});

const AS_AUTHORED: AgResponsiveLayoutBreakpoint[] = [];

// The dashboard opens with no panels; editing adds Studio's field list, its agents, and the
// widget settings. Studio shows its AI panel in edit mode only. Each panel is a column of its
// own, so a fourth (filters) left the canvas a sliver on a laptop screen.
const PANELS: AgPanelConfig = {
  view: {},
  edit: { left: ["data"], right: ["ai", "edit"] },
};

/** The analytics dashboard over the API's rows; AG Studio loads with this module and nowhere else. */
export function AnalyticsDashboard({ rows }: { rows: AnalyticsRows }) {
  const data = useMemo(() => analyticsSources(rows), [rows]);
  // One rule picks both the layout and the height that fits it: the console's grids switch
  // at the same width. The heights are the layout's rows of 16 pixels plus Studio's padding.
  const wide = useMediaQuery("(min-width: 768px)");
  const [editing, setEditing] = useState(false);
  // The agents spend the team's Claude credit, so they open with the chat's access code.
  const { chatKey } = useShopper();

  return (
    <>
      {wide && (
        <div className="mb-3 flex items-center justify-end gap-3">
          {editing && (
            <p className="text-sm text-muted-foreground">
              Drag fields onto the canvas, change any widget, or ask the agents
              in the chat panel. Changes last until the page reloads.
            </p>
          )}
          <Button
            onClick={() => {
              setEditing(!editing);
            }}
            size="sm"
            variant="outline"
          >
            {editing ? <Check aria-hidden /> : <Pencil aria-hidden />}
            {editing ? "Done Editing" : "Edit and Ask"}
          </Button>
        </div>
      )}
      {wide && editing && chatKey === null && (
        <div className="mb-3 flex max-w-md rounded-xl border bg-card">
          <AssistantAccess
            action="Open the Agents"
            reason="The dashboard's agents run on Claude and open with the chat access key that was shared with you."
          />
        </div>
      )}
      <AgStudioProvider licenseKey={rows.license_key}>
        <AgStudio
          ai={chatKey === null ? undefined : analyticsAi}
          className={wide ? "h-[70rem]" : "h-[148rem]"}
          data={data}
          initialState={ANALYTICS_REPORT}
          loadThemeGoogleFonts={false}
          mode={wide && editing ? "edit" : "view"}
          panels={PANELS}
          responsiveLayoutBreakpoints={wide ? AS_AUTHORED : STACKED_LAYOUT}
          theme={theme}
        />
      </AgStudioProvider>
    </>
  );
}
