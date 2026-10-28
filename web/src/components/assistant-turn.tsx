import { useQuery } from "@tanstack/react-query";
import {
  Check,
  CircleCheck,
  CircleX,
  Clock,
  ExternalLink,
  LoaderCircle,
  ShoppingBag,
  X,
} from "lucide-react";
import { type ReactNode } from "react";
import Markdown from "react-markdown";
import { Link } from "react-router";

import type {
  MandateCard,
  OrderCard,
  ProductCard,
  ToolCallEvent,
  ToolResultEvent,
  TurnRequest,
} from "@/api/client";
import type { TurnEvent } from "@/hooks/use-assistant";

import { mandateOptions } from "@/api/client/@tanstack/react-query.gen";
import { AgentIcon } from "@/components/agent-icon";
import { Button } from "@/components/ui/button";
import { formatAmount, formatMoney } from "@/money";
import { photoSrcSet } from "@/product-photos";
import { TOOL_LABELS } from "@/tool-labels";

const PENDING_MANDATE_REFETCH_MS = 3_000;
// The window the consent page opens in; a second request reuses it instead of piling up tabs.
const CONSENT_WINDOW = "palisade-consent";
// Claude writes light Markdown; only these parts of it reach the page, as text, never as HTML.
const MARKDOWN_ELEMENTS = ["p", "strong", "em", "ul", "ol", "li", "br"];

const MANDATE_HEADINGS: Record<MandateCard["kind"], string> = {
  cart: "Approve this purchase",
  intent: "Let the agent shop for you",
};

const ORDER_WORDS: Record<OrderCard["status"], string> = {
  creating: "Being placed",
  declined: "Declined",
  failed: "Not confirmed",
  authorized: "Payment authorized",
  capture_pending: "Payment pending",
  captured: "Paid",
  voided: "Voided",
  refunded: "Refunded",
  reversed: "Reversed",
};

function Products({ products }: { products: ProductCard[] }) {
  return (
    <ul className="grid grid-cols-2 gap-2">
      {products.map((product) => (
        <li
          className="flex gap-2 rounded-lg border bg-card p-2 text-xs"
          key={product.id}
        >
          <img
            alt=""
            className="size-14 shrink-0 rounded-md bg-muted object-cover"
            decoding="async"
            loading="lazy"
            sizes="56px"
            src={product.image_path}
            srcSet={photoSrcSet(product.image_path)}
          />
          <div className="min-w-0">
            <p className="truncate font-semibold tracking-wide text-primary uppercase">
              {product.brand}
            </p>
            <p className="line-clamp-2 font-medium">{product.name}</p>
            <p className="text-muted-foreground">
              {product.category} · {formatMoney(product.price.value)}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** The agent's request to the shopper, with its answer followed live while the chat waits on it. */
function Mandate({
  mandate,
  answer,
  awaited,
}: {
  mandate: MandateCard;
  answer: MandateCard["status"] | undefined;
  awaited: boolean;
}) {
  const current = useQuery({
    ...mandateOptions({ path: { mandate_id: mandate.id } }),
    enabled: awaited && answer === undefined && mandate.status === "pending",
    refetchInterval: (query) =>
      query.state.data?.status === "pending"
        ? PENDING_MANDATE_REFETCH_MS
        : false,
  });
  const status = answer ?? current.data?.status ?? mandate.status;
  // Only the card of the request itself carries the consent link with its key; a later check
  // of the same mandate shows its state without one.
  const link = mandate.consent_path;

  return (
    <div className="rounded-lg border bg-card p-3 text-sm">
      <p className="font-semibold">{MANDATE_HEADINGS[mandate.kind]}</p>
      <p className="mt-0.5 text-muted-foreground">
        Up to {formatAmount(mandate.cap.value)} {mandate.cap.currency_code}
        {mandate.kind === "intent" && " in all, for 24 hours"}. Signed on
        Palisade&apos;s consent page and kept with the order.
      </p>
      {status === "pending" && typeof link === "string" ? (
        <Button
          className="mt-3 h-9 w-full"
          onClick={() => {
            window.open(link, CONSENT_WINDOW);
          }}
        >
          Review and Approve
          <ExternalLink aria-hidden />
          <span className="sr-only">(opens in a new window)</span>
        </Button>
      ) : status === "pending" ? (
        <p className="mt-3 flex items-center gap-1.5 font-medium">
          <Clock aria-hidden className="size-4 text-muted-foreground" />
          Waiting for Your Approval
        </p>
      ) : (
        <p className="mt-3 flex items-center gap-1.5 font-medium">
          {status === "signed" ? (
            <>
              <CircleCheck aria-hidden className="size-4 text-emerald-700" />
              Approved
            </>
          ) : status === "declined" ? (
            <>
              <CircleX aria-hidden className="size-4 text-muted-foreground" />
              Declined
            </>
          ) : (
            <>
              <Clock aria-hidden className="size-4 text-muted-foreground" />
              Expired
            </>
          )}
        </p>
      )}
    </div>
  );
}

function OrderPlaced({ order }: { order: OrderCard }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card p-3 text-sm">
      <ShoppingBag aria-hidden className="size-5 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          Order {order.number} · {ORDER_WORDS[order.status]}
        </p>
        <p className="text-muted-foreground">
          {formatAmount(order.total.value)} {order.total.currency_code}
        </p>
      </div>
      <Button asChild size="sm" variant="outline">
        <Link to={`/shop/orders/${order.number}`}>Receipt</Link>
      </Button>
    </div>
  );
}

function Attachment({
  attachment,
  settled,
  awaited,
}: {
  attachment: NonNullable<ToolResultEvent["attachment"]>;
  settled: Map<string, MandateCard["status"]>;
  awaited: null | string;
}) {
  switch (attachment.type) {
    case "products":
      return <Products products={attachment.products} />;
    case "cart":
      return (
        <p className="text-sm text-muted-foreground">
          Your cart holds {attachment.cart.items}{" "}
          {attachment.cart.items === 1 ? "pair" : "pairs"},{" "}
          {formatAmount(attachment.cart.total.value)} in all. Open the bag to
          change it.
        </p>
      );
    case "mandate":
      return (
        <Mandate
          answer={settled.get(attachment.mandate.id)}
          awaited={attachment.mandate.id === awaited}
          mandate={attachment.mandate}
        />
      );
    case "order":
      return <OrderPlaced order={attachment.order} />;
  }
}

/** A tool call as one line: what the agent is doing, then how it went. */
function ToolStep({
  call,
  result,
  settled,
  awaited,
}: {
  call: ToolCallEvent;
  result: ToolResultEvent | undefined;
  settled: Map<string, MandateCard["status"]>;
  awaited: null | string;
}) {
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {result === undefined ? (
          <LoaderCircle
            aria-hidden
            className="size-3.5 motion-safe:animate-spin"
          />
        ) : result.ok ? (
          <Check aria-hidden className="size-3.5 text-emerald-700" />
        ) : (
          <X aria-hidden className="size-3.5 text-destructive" />
        )}
        <span>
          {TOOL_LABELS[call.tool]}
          {result !== undefined && `: ${result.summary}`}
        </span>
      </p>
      {result?.attachment && (
        <Attachment
          attachment={result.attachment}
          awaited={awaited}
          settled={settled}
        />
      )}
    </div>
  );
}

/** One thing the agent says, beside its avatar, so every message reads as the agent's. */
export function AgentRow({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <AgentIcon className="size-5" />
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** A stable key for each event: a tool call has an id, and a text block is the n-th of its turn. */
function keyed(events: TurnEvent[]): { key: string; event: TurnEvent }[] {
  let texts = 0;
  return events.map((event) => ({
    event,
    key:
      event.type === "text"
        ? `text-${String(++texts)}`
        : `${event.type}-${event.id}`,
  }));
}

/** One exchange as the panel shows it: the shopper on the right, the agent on the left. */
export function AssistantTurn({
  request,
  events,
  settled,
  awaited,
  streaming,
}: {
  request: TurnRequest;
  events: TurnEvent[];
  settled: Map<string, MandateCard["status"]>;
  awaited: null | string;
  streaming: boolean;
}) {
  const results = new Map(
    events
      .filter((event): event is ToolResultEvent => event.type === "tool_result")
      .map((event) => [event.id, event]),
  );

  return (
    <li className="space-y-3">
      {request.kind === "message" ? (
        <p className="ml-10 w-fit justify-self-end rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-sm whitespace-pre-wrap text-primary-foreground">
          {request.text}
        </p>
      ) : (
        <p className="text-center text-xs text-muted-foreground">
          {request.text}
        </p>
      )}
      {keyed(events).map(({ key, event }) => {
        switch (event.type) {
          case "text":
            return (
              <AgentRow key={key}>
                <div className="w-fit max-w-full rounded-2xl rounded-tl-sm bg-secondary px-3 py-2 text-sm [&_li+li]:mt-0.5 [&_ol]:mt-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_p+p]:mt-2 [&_strong]:font-semibold [&_ul]:mt-1 [&_ul]:list-disc [&_ul]:pl-5">
                  <Markdown
                    allowedElements={MARKDOWN_ELEMENTS}
                    unwrapDisallowed
                  >
                    {event.text}
                  </Markdown>
                </div>
              </AgentRow>
            );
          case "tool_call":
            return (
              <div className="pl-10.5" key={key}>
                <ToolStep
                  awaited={awaited}
                  call={event}
                  result={results.get(event.id)}
                  settled={settled}
                />
              </div>
            );
          case "tool_result":
            return null;
        }
      })}
      {streaming && events.at(-1)?.type !== "text" && (
        <AgentRow>
          <p
            aria-hidden
            className="flex w-fit gap-1 rounded-2xl rounded-tl-sm bg-secondary px-3 py-3"
          >
            {[0, 1, 2].map((dot) => (
              <span
                className="size-1.5 rounded-full bg-muted-foreground motion-safe:animate-bounce"
                key={dot}
                style={{ animationDelay: `${String(dot * 150)}ms` }}
              />
            ))}
          </p>
        </AgentRow>
      )}
    </li>
  );
}
