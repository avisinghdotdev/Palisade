import type { Order } from "@/api/client";

import { Badge } from "@/components/ui/badge";
import { STATUS_LABELS } from "@/order-labels";

const TONES: Record<Order["status"], string> = {
  creating: "",
  declined: "border-destructive/40 bg-destructive/5 text-destructive",
  failed: "border-destructive/40 bg-destructive/5 text-destructive",
  authorized: "border-amber-600/40 bg-amber-50 text-amber-800",
  capture_pending: "border-amber-600/40 bg-amber-50 text-amber-800",
  captured: "border-emerald-600/40 bg-emerald-50 text-emerald-800",
  voided: "",
  refunded: "",
  reversed: "border-destructive/40 bg-destructive/5 text-destructive",
};

export function OrderStatus({ status }: { status: Order["status"] }) {
  return (
    <Badge className={TONES[status]} variant="outline">
      {STATUS_LABELS[status]}
    </Badge>
  );
}
