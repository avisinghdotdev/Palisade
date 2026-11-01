import type { DisputeSummary } from "@/api/client";

import { Badge } from "@/components/ui/badge";
import { OUTCOME_LABELS, STATUS_LABELS } from "@/dispute-labels";

const WARNING = "border-amber-600/40 bg-amber-50 text-amber-800";
const WON = "border-emerald-600/40 bg-emerald-50 text-emerald-800";
const LOST = "border-destructive/40 bg-destructive/5 text-destructive";

/** Where the dispute stands; once resolved, who won. */
export function DisputeStatus({
  status,
  outcome,
}: Pick<DisputeSummary, "outcome" | "status">) {
  if (status === "RESOLVED" && outcome !== null) {
    const tone =
      outcome === "RESOLVED_SELLER_FAVOUR" || outcome === "CANCELED_BY_BUYER"
        ? WON
        : outcome === "RESOLVED_BUYER_FAVOUR"
          ? LOST
          : "";
    return (
      <Badge className={tone} variant="outline">
        {OUTCOME_LABELS[outcome]}
      </Badge>
    );
  }
  return (
    <Badge
      className={status === "WAITING_FOR_SELLER_RESPONSE" ? WARNING : ""}
      variant="outline"
    >
      {STATUS_LABELS[status]}
    </Badge>
  );
}
