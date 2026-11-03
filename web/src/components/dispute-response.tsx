import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCheck, FileText, LoaderCircle, ShieldAlert } from "lucide-react";

import type {
  Dispute,
  DisputeResponse,
  Evidence,
  EvidenceDocumentData,
} from "@/api/client";

import {
  disputeActivityQueryKey,
  disputeQueryKey,
  disputeResponseOptions,
  disputeResponseQueryKey,
  disputesQueryKey,
  fileEvidenceMutation,
  prepareResponseMutation,
} from "@/api/client/@tanstack/react-query.gen";
import { client } from "@/api/client/client.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EVIDENCE_TYPE_LABELS } from "@/dispute-labels";
import { formatUtc } from "@/money";
import { errorDetail } from "@/shopper";

const STATUS_LABELS: Record<Evidence["status"], string> = {
  prepared: "Ready for Review",
  approved: "Approved, Waiting for PayPal",
  filed: "Filed with PayPal",
  refused: "Refused by PayPal",
};

const MODE_NOTES: Record<DisputeResponse["filing_mode"], string> = {
  review:
    "Review and file: Palisade prepares the evidence and the merchant sends it.",
  automatic:
    "File automatically: Palisade sends the evidence as PayPal takes it.",
};

/** The open answer, if any: the one a button acts on. */
function openEvidence(response: DisputeResponse): Evidence | undefined {
  return response.evidence.findLast(
    (row) => row.status === "prepared" || row.status === "approved",
  );
}

/** The record's own URL on the API, which the browser opens in a frame on the same origin. */
function documentUrl(disputeId: string, evidenceId: string): string {
  return client.buildUrl<EvidenceDocumentData>({
    url: "/api/disputes/{dispute_id}/evidence/{evidence_id}/document",
    path: { dispute_id: disputeId, evidence_id: evidenceId },
  });
}

/** The record PDF: opened in the browser's own viewer, or saved. */
function RecordDocument({
  disputeId,
  evidence,
}: {
  disputeId: string;
  evidence: Evidence;
}) {
  const url = documentUrl(disputeId, evidence.id);

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <Button asChild size="sm" variant="outline">
        <a href={url} rel="noopener" target="_blank">
          <FileText aria-hidden />
          Open the Record PDF
        </a>
      </Button>
      <Button asChild size="sm" variant="ghost">
        <a download={evidence.document_name} href={url}>
          Download
        </a>
      </Button>
      <span className="font-mono text-xs break-all text-muted-foreground">
        SHA-256 {evidence.document_sha256}
      </span>
    </div>
  );
}

function EvidenceCard({
  disputeId,
  evidence,
}: {
  disputeId: string;
  evidence: Evidence;
}) {
  return (
    <li className="rounded-xl border bg-card px-4 py-3">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-medium">
          {EVIDENCE_TYPE_LABELS[evidence.evidence_type]}
        </span>
        <Badge
          className={
            evidence.status === "refused"
              ? "border-destructive/40 text-destructive"
              : evidence.status === "filed"
                ? "border-emerald-600/40 bg-emerald-50 text-emerald-800"
                : ""
          }
          variant="outline"
        >
          {STATUS_LABELS[evidence.status]}
        </Badge>
        <span className="text-xs text-muted-foreground">
          Prepared {formatUtc(evidence.prepared_at)}
          {evidence.filed_at !== null &&
            `, filed ${formatUtc(evidence.filed_at)}`}
        </span>
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {evidence.prompt === null
          ? "Notes written by Palisade from the shipment record."
          : "Notes written by Claude from the facts record, and nothing else."}
      </p>
      <p className="mt-2 text-sm whitespace-pre-line">{evidence.notes}</p>
      <RecordDocument disputeId={disputeId} evidence={evidence} />
      {evidence.request_id !== null && (
        <p className="mt-3 font-mono text-xs break-all text-muted-foreground">
          PayPal-Request-Id {evidence.request_id}
          {evidence.status_code !== null &&
            ` · HTTP ${String(evidence.status_code)}`}
          {evidence.debug_id !== null && ` · debug id ${evidence.debug_id}`}
        </p>
      )}
    </li>
  );
}

/** What Palisade does about the dispute: the evidence it prepared, and the merchant's say. */
export function DisputeResponsePanel({ dispute }: { dispute: Dispute }) {
  const path = { dispute_id: dispute.id };
  const queryClient = useQueryClient();
  const response = useQuery({
    ...disputeResponseOptions({ path }),
    refetchInterval: dispute.status === "RESOLVED" ? false : 15_000,
  });
  const store = (changed: DisputeResponse) => {
    queryClient.setQueryData(disputeResponseQueryKey({ path }), changed);
    void queryClient.invalidateQueries({ queryKey: disputeQueryKey({ path }) });
    void queryClient.invalidateQueries({
      queryKey: disputeActivityQueryKey({ path }),
    });
    void queryClient.invalidateQueries({ queryKey: disputesQueryKey() });
  };
  const prepare = useMutation({
    ...prepareResponseMutation(),
    onSuccess: store,
  });
  const file = useMutation({ ...fileEvidenceMutation(), onSuccess: store });
  const busy = prepare.isPending || file.isPending;
  const data = response.data;
  const open = data === undefined ? undefined : openEvidence(data);

  return (
    <section aria-labelledby="response">
      <h2
        className="flex items-center gap-2 text-base font-semibold"
        id="response"
      >
        <FileCheck aria-hidden className="size-4 text-muted-foreground" />
        Palisade&apos;s Response
      </h2>
      {response.isPending && (
        <p className="mt-3 text-sm text-muted-foreground" role="status">
          Loading the response…
        </p>
      )}
      {response.isError && (
        <p className="mt-3 text-sm text-destructive">
          The response did not load.
        </p>
      )}
      {data !== undefined && (
        <>
          <p className="mt-1 text-xs text-muted-foreground">
            {MODE_NOTES[data.filing_mode]}
          </p>
          <div
            className={`mt-3 flex items-start gap-3 rounded-xl border px-4 py-3 ${
              data.action === "merchant"
                ? "border-amber-600/40 bg-amber-50 text-amber-900"
                : "bg-card"
            }`}
          >
            {data.action === "merchant" && (
              <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm">{data.why}</p>
              {(data.action === "prepare" ||
                data.action === "review" ||
                data.action === "escalate" ||
                data.action === "file") && (
                <Button
                  className="mt-3"
                  disabled={busy}
                  onClick={() => {
                    if (data.action === "prepare") {
                      prepare.mutate({ path });
                    } else if (open !== undefined) {
                      file.mutate({
                        path: { ...path, evidence_id: open.id },
                      });
                    }
                  }}
                  size="sm"
                >
                  {data.action === "prepare"
                    ? "Prepare the Response"
                    : data.action === "review"
                      ? "File with PayPal"
                      : "File Now"}
                </Button>
              )}
              {busy && (
                <p
                  className="mt-2 flex items-center gap-2 text-sm text-muted-foreground"
                  role="status"
                >
                  <LoaderCircle
                    aria-hidden
                    className="size-4 motion-safe:animate-spin"
                  />
                  {prepare.isPending
                    ? "Claude is writing the notes and Palisade the record…"
                    : "Sending to PayPal…"}
                </p>
              )}
              {prepare.isError && (
                <p className="mt-2 text-sm text-destructive" role="alert">
                  {errorDetail(prepare.error)}
                </p>
              )}
              {file.isError && (
                <p className="mt-2 text-sm text-destructive" role="alert">
                  {errorDetail(file.error)}
                </p>
              )}
            </div>
          </div>
          {data.evidence.length > 0 && (
            <ul className="mt-3 grid gap-3">
              {data.evidence.map((evidence) => (
                <EvidenceCard
                  disputeId={dispute.id}
                  evidence={evidence}
                  key={evidence.id}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
