import { ShieldCheck } from "lucide-react";

import type { VerifyMandateData } from "@/api/client";

import { client } from "@/api/client/client.gen";
import { Button } from "@/components/ui/button";

/** The mandate's public verification: JSON anyone can read, with no console and no key. */
export function MandateVerifyLink({ mandateId }: { mandateId: string }) {
  const url = client.buildUrl<VerifyMandateData>({
    url: "/api/mandates/{mandate_id}/verify",
    path: { mandate_id: mandateId },
  });

  return (
    <Button asChild size="sm" variant="outline">
      <a href={url} rel="noopener" target="_blank">
        <ShieldCheck aria-hidden />
        Verify the Mandate
      </a>
    </Button>
  );
}
