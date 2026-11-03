import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Policy } from "@/api/client";

import {
  policyOptions,
  policyQueryKey,
  setDisputeFilingMutation,
} from "@/api/client/@tanstack/react-query.gen";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { errorDetail } from "@/shopper";

type Mode = Policy["dispute_filing"];

const MODES: { mode: Mode; label: string; note: string }[] = [
  {
    mode: "review",
    label: "Review and file",
    note: "Palisade prepares the evidence; the merchant reads it and files it.",
  },
  {
    mode: "automatic",
    label: "File automatically",
    note: "Palisade files the evidence as soon as PayPal takes it.",
  },
];

function isMode(value: string): value is Mode {
  return MODES.some((option) => option.mode === value);
}

/** The merchant's choice of how Palisade answers disputes, kept on the policy in force. */
export function FilingMode() {
  const queryClient = useQueryClient();
  const policy = useQuery(policyOptions());
  const change = useMutation({
    ...setDisputeFilingMutation(),
    onSuccess: (changed) => {
      queryClient.setQueryData(policyQueryKey(), changed);
    },
  });
  const current = change.isPending
    ? change.variables.body.mode
    : policy.data?.dispute_filing;

  return (
    <div className="grid gap-1">
      <label className="text-xs text-muted-foreground" htmlFor="dispute-filing">
        How disputes are answered
      </label>
      <Select
        disabled={policy.data === undefined || change.isPending}
        onValueChange={(value) => {
          if (isMode(value) && value !== policy.data?.dispute_filing) {
            change.mutate({ body: { mode: value } });
          }
        }}
        value={current}
      >
        <SelectTrigger id="dispute-filing" size="sm">
          <SelectValue placeholder="Loading…" />
        </SelectTrigger>
        <SelectContent>
          {MODES.map((option) => (
            <SelectItem key={option.mode} value={option.mode}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">
        {change.isError
          ? errorDetail(change.error)
          : MODES.find((option) => option.mode === current)?.note}
      </p>
    </div>
  );
}
