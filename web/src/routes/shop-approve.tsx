import type {
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";

import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
} from "@simplewebauthn/browser";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleCheck,
  CircleX,
  Clock,
  KeyRound,
  LoaderCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import type { Mandate } from "@/api/client";

import {
  approveMandate,
  declineMandate,
  mandatePasskeyOptions,
  registerPasskey,
  startPasskeyRegistration,
} from "@/api/client";
import {
  mandateOptions,
  mandateQueryKey,
} from "@/api/client/@tanstack/react-query.gen";
import { PalisadeBadge } from "@/components/palisade-badge";
import { ShopHeader } from "@/components/shop-header";
import { Button } from "@/components/ui/button";
import { useHydrated } from "@/hooks/use-hydrated";
import { formatAmount, formatLocal } from "@/money";
import { pageTitle } from "@/page-title";
import { errorDetail, passkey } from "@/shopper";

import type { Route } from "./+types/shop-approve";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Approve a Purchase", "Shop") },
];

/** The mandate's answer is final, so the page stops asking the API once it is not pending. */
const PENDING_REFETCH_MS = 10_000;
/** Long enough to read the outcome before a window the chat opened closes itself. */
const CLOSE_DELAY_MS = 1_800;

const SIGNER_LABELS: Record<NonNullable<Mandate["signed_by"]>, string> = {
  passkey: "your passkey",
  demo_signer: "the demo signer (simulated)",
};

/** Registers a passkey the first time, then signs the mandate's hash with it. */
async function signWithPasskey(mandateId: string): Promise<void> {
  let passkeyId = passkey.get();
  if (passkeyId === null) {
    const start = await startPasskeyRegistration({ throwOnError: true });
    // The API hands the WebAuthn JSON through untyped; the library knows its exact shape.
    const credential = await startRegistration({
      optionsJSON: start.data
        .options as unknown as PublicKeyCredentialCreationOptionsJSON,
    });
    const registered = await registerPasskey({
      body: { challenge: start.data.challenge, credential: { ...credential } },
      throwOnError: true,
    });
    passkeyId = registered.data.id;
    passkey.set(passkeyId);
  }
  const options = await mandatePasskeyOptions({
    path: { mandate_id: mandateId },
    query: { passkey_id: passkeyId },
    throwOnError: true,
  });
  const assertion = await startAuthentication({
    optionsJSON:
      options.data as unknown as PublicKeyCredentialRequestOptionsJSON,
  });
  await approveMandate({
    path: { mandate_id: mandateId },
    body: {
      signed_by: "passkey",
      passkey_id: passkeyId,
      credential: { ...assertion },
    },
    throwOnError: true,
  });
}

/** What the agent asks for, in the buyer's words. */
function Request({ mandate }: { mandate: Mandate }) {
  if (mandate.kind === "cart") {
    return (
      <>
        <p className="text-sm text-muted-foreground">
          {mandate.agent.name} wants to buy these for you now, paying from{" "}
          {mandate.wallet_email}.
        </p>
        <ul className="mt-4 divide-y rounded-lg border bg-secondary/40 px-4">
          {mandate.lines.map((line) => (
            <li
              className="flex items-baseline justify-between gap-3 py-2 text-sm"
              key={`${line.product_id}/${line.size}`}
            >
              <span>
                {line.name}, US {line.size} × {line.quantity}
              </span>
              <span className="tabular-nums">
                {formatAmount(
                  String(Number(line.unit_price.value) * line.quantity),
                )}
              </span>
            </li>
          ))}
          <li className="flex items-baseline justify-between py-2 text-sm font-semibold">
            <span>Up to</span>
            <span className="tabular-nums">
              {formatAmount(mandate.cap.value)} {mandate.cap.currency_code}
            </span>
          </li>
        </ul>
      </>
    );
  }
  return (
    <>
      <p className="text-sm text-muted-foreground">
        {mandate.agent.name} asks to shop for you without checking back each
        time, paying from {mandate.wallet_email}.
      </p>
      <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-lg border bg-secondary/40 px-4 py-3 text-sm">
        <dt className="text-muted-foreground">Up to</dt>
        <dd className="font-semibold tabular-nums">
          {formatAmount(mandate.cap.value)} {mandate.cap.currency_code} in all
        </dd>
        <dt className="text-muted-foreground">On</dt>
        <dd>
          {mandate.categories.length === 0
            ? "Any shoes at Ridgeline Running"
            : `${mandate.categories.join(", ")} shoes at Ridgeline Running`}
        </dd>
        <dt className="text-muted-foreground">Until</dt>
        <dd>{formatLocal(mandate.expires_at)}</dd>
      </dl>
    </>
  );
}

/** How the page ends: a window the chat opened closes itself; a tab opened by hand says so. */
function useClosing(): boolean {
  // This route is never prerendered, so the window is there to ask on the first render.
  const [closing] = useState(() => window.opener !== null);
  useEffect(() => {
    if (!closing) return;
    const timer = setTimeout(() => {
      window.close();
    }, CLOSE_DELAY_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [closing]);
  return closing;
}

/** Where the buyer goes next: a window on its way to closing says so and shows it is working. */
function Next({ closing }: { closing: boolean }) {
  return closing ? (
    <p className="mt-4 flex items-center justify-center gap-2 text-sm font-medium">
      <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" />
      Taking you back to Ridgeline Running…
    </p>
  ) : (
    <p className="mt-4 text-sm text-muted-foreground">
      You can close this tab and go back to the chat.
    </p>
  );
}

function Outcome({ mandate }: { mandate: Mandate }) {
  const closing = useClosing();
  if (mandate.status === "signed" && mandate.signed_by !== null) {
    return (
      <div className="text-center" role="status">
        <CircleCheck aria-hidden className="mx-auto size-10 text-emerald-700" />
        <h1 className="mt-3 text-lg font-semibold tracking-tight">Approved</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          You signed this with {SIGNER_LABELS[mandate.signed_by]}.{" "}
          {mandate.agent.name} finishes the purchase from here.
        </p>
        <Next closing={closing} />
      </div>
    );
  }
  const expired = mandate.status === "expired";
  return (
    <div className="text-center" role="status">
      {expired ? (
        <Clock aria-hidden className="mx-auto size-10 text-muted-foreground" />
      ) : (
        <CircleX
          aria-hidden
          className="mx-auto size-10 text-muted-foreground"
        />
      )}
      <h1 className="mt-3 text-lg font-semibold tracking-tight">
        {expired ? "This Request has Expired" : "Declined"}
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        {expired
          ? `Nothing was bought. ${mandate.agent.name} has to ask again.`
          : "Nothing will be bought. Tell the assistant what to change."}
      </p>
      <Next closing={closing} />
    </div>
  );
}

/** Where the buyer answers an agent's request: approve with a passkey or the demo signer. */
export default function ShopApprove({ params }: Route.ComponentProps) {
  const queryClient = useQueryClient();
  const hydrated = useHydrated();
  const mandate = useQuery({
    ...mandateOptions({ path: { mandate_id: params.mandateId } }),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? PENDING_REFETCH_MS : false,
  });
  const [problem, setProblem] = useState<null | string>(null);
  const refresh = async (updated: Mandate) => {
    queryClient.setQueryData(
      mandateQueryKey({ path: { mandate_id: updated.id } }),
      updated,
    );
    await Promise.resolve();
  };

  const withPasskey = useMutation({
    mutationFn: async () => {
      await signWithPasskey(params.mandateId);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: mandateQueryKey({ path: { mandate_id: params.mandateId } }),
      });
    },
    onError: (error) => {
      setProblem(
        error instanceof Error && error.name === "NotAllowedError"
          ? "The passkey prompt was closed. Try again, or use the demo signer."
          : error instanceof Error && error.name === "SecurityError"
            ? "Passkeys do not work at this web address. Use the demo signer here, or open the hosted site."
            : errorDetail(error),
      );
    },
  });
  const withDemoSigner = useMutation({
    mutationFn: async () => {
      const approved = await approveMandate({
        path: { mandate_id: params.mandateId },
        body: { signed_by: "demo_signer" },
        throwOnError: true,
      });
      return approved.data;
    },
    onSuccess: refresh,
    onError: (error) => {
      setProblem(errorDetail(error));
    },
  });
  const decline = useMutation({
    mutationFn: async () => {
      const declined = await declineMandate({
        path: { mandate_id: params.mandateId },
        throwOnError: true,
      });
      return declined.data;
    },
    onSuccess: refresh,
    onError: (error) => {
      setProblem(errorDetail(error));
    },
  });

  const busy =
    withPasskey.isPending || withDemoSigner.isPending || decline.isPending;
  const passkeysAvailable = hydrated && browserSupportsWebAuthn();

  return (
    <div className="theme-shop flex min-h-dvh flex-col bg-secondary text-foreground">
      <ShopHeader to="/shop">
        <PalisadeBadge>Protected by Palisade</PalisadeBadge>
      </ShopHeader>
      <main className="flex flex-1 items-start justify-center px-4 py-10">
        <section className="w-full max-w-md rounded-xl border bg-card p-8 shadow-sm">
          {mandate.isPending && (
            <p
              className="text-center text-sm text-muted-foreground"
              role="status"
            >
              Loading the request…
            </p>
          )}
          {mandate.isError && (
            <div className="text-center">
              <h1 className="text-lg font-semibold">Request not Found</h1>
              <p className="mt-2 text-sm text-muted-foreground">
                {errorDetail(mandate.error)}
              </p>
              <Button asChild className="mt-6">
                <Link to="/shop">Back to the Shop</Link>
              </Button>
            </div>
          )}
          {mandate.data !== undefined && mandate.data.status !== "pending" && (
            <Outcome mandate={mandate.data} />
          )}
          {mandate.data?.status === "pending" && (
            <>
              <h1 className="text-lg font-semibold tracking-tight">
                {mandate.data.kind === "cart"
                  ? "Approve this Purchase"
                  : "Let Your Agent Shop for You"}
              </h1>
              <div className="mt-2">
                <Request mandate={mandate.data} />
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                Ridgeline Running uses Palisade to keep your signed approval
                with the order, so a dispute can show what you agreed to.
                Expires {formatLocal(mandate.data.expires_at)}.
              </p>
              {problem !== null && (
                <p className="mt-4 text-sm text-destructive" role="alert">
                  {problem}
                </p>
              )}
              <div className="mt-6 flex flex-col gap-3">
                {passkeysAvailable && (
                  <Button
                    className="h-11"
                    disabled={busy}
                    onClick={() => {
                      setProblem(null);
                      withPasskey.mutate();
                    }}
                  >
                    {withPasskey.isPending ? (
                      <LoaderCircle
                        aria-hidden
                        className="motion-safe:animate-spin"
                      />
                    ) : (
                      <KeyRound aria-hidden />
                    )}
                    Approve with Passkey
                  </Button>
                )}
                <Button
                  className="h-11"
                  disabled={busy}
                  onClick={() => {
                    setProblem(null);
                    withDemoSigner.mutate();
                  }}
                  variant={passkeysAvailable ? "outline" : "default"}
                >
                  {withDemoSigner.isPending && (
                    <LoaderCircle
                      aria-hidden
                      className="motion-safe:animate-spin"
                    />
                  )}
                  Approve with Demo Signer (Simulated)
                </Button>
                <p className="text-xs text-muted-foreground">
                  The demo signer is a key Palisade holds for this demo, not a
                  passkey of yours; the record says so.
                  {!passkeysAvailable &&
                    " Passkeys are not available in this browser."}
                </p>
                <Button
                  className="h-11"
                  disabled={busy}
                  onClick={() => {
                    setProblem(null);
                    decline.mutate();
                  }}
                  variant="outline"
                >
                  Decline
                </Button>
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
