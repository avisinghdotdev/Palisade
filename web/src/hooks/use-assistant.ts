import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import type {
  Conversation,
  MandateCard,
  MandateOutcome,
  ShopperMessage,
  TextEvent,
  ToolCallEvent,
  ToolResultEvent,
  Turn,
  TurnRequest,
} from "@/api/client";

import { createConversation, sendMessage } from "@/api/client";
import {
  cartQueryKey,
  conversationOptions,
  conversationQueryKey,
  mandateOptions,
} from "@/api/client/@tanstack/react-query.gen";
import { client } from "@/api/client/client.gen";
import { storedChatKey, useShopper } from "@/shopper";

// The chat endpoints declare the access code as their security scheme; the client reads it
// from storage for those requests and sends nothing extra anywhere else.
client.setConfig({ auth: storedChatKey });

export type TurnEvent = TextEvent | ToolCallEvent | ToolResultEvent;
export type Incoming = MandateOutcome | ShopperMessage;

/** The turn in progress: what the shopper sent and what has streamed back so far. */
export interface LiveTurn {
  request: TurnRequest;
  events: TurnEvent[];
}

/** How often the panel asks after a mandate the shopper has been sent to approve. */
const PENDING_MANDATE_REFETCH_MS = 3_000;
const CHAT_TITLE_LENGTH = 80;

const PROBLEMS: Record<number, string> = {
  401: "The access code no longer works. Enter it again.",
  404: "This conversation is gone. Start a new one.",
  409: "The assistant is still answering. Give it a moment.",
};

/** The streaming client reports a refused request as an error naming the status; the message for it. */
function problemFor(error: unknown): string {
  const status = Number(
    /^SSE failed: (\d+)/.exec(error instanceof Error ? error.message : "")?.[1],
  );
  return PROBLEMS[status] ?? "The connection dropped. Send your message again.";
}

/** The turn's events with each run of streamed text joined into one block, as the API stores them. */
function appendEvent(events: TurnEvent[], event: TurnEvent): TurnEvent[] {
  const last = events.at(-1);
  if (event.type === "text" && last?.type === "text") {
    return [
      ...events.slice(0, -1),
      { type: "text", text: last.text + event.text },
    ];
  }
  return [...events, event];
}

function requestFor(incoming: Incoming): TurnRequest {
  return incoming.type === "message"
    ? { kind: "message", text: incoming.text, mandate_id: null }
    : {
        kind: "mandate_approved",
        text: "Checking your answer…",
        mandate_id: incoming.mandate_id,
      };
}

/** A mandate's answer as a later turn recorded it, so a settled request is never asked about again. */
const ANSWERS: Partial<Record<TurnRequest["kind"], MandateCard["status"]>> = {
  mandate_approved: "signed",
  mandate_declined: "declined",
  mandate_expired: "expired",
};

/** The mandates the conversation has answered, by id. */
export function settledMandates(
  turns: Turn[],
): Map<string, MandateCard["status"]> {
  const settled = new Map<string, MandateCard["status"]>();
  for (const turn of turns) {
    const status = ANSWERS[turn.request.kind];
    const id = turn.request.mandate_id;
    if (typeof id === "string" && status !== undefined) settled.set(id, status);
  }
  return settled;
}

/**
 * The mandate the latest turn asked for and nobody has answered; it is being waited on. A
 * later message from the shopper moves on from an earlier request, which is then left alone.
 */
function awaitedMandate(turns: Turn[], live: LiveTurn | null): null | string {
  const latest = live ?? turns.at(-1);
  if (latest === undefined || latest.request.mandate_id) return null;
  const asked = latest.events
    .filter(
      (event): event is ToolResultEvent =>
        event.type === "tool_result" && event.attachment?.type === "mandate",
    )
    .map((event) =>
      event.attachment?.type === "mandate" ? event.attachment.mandate : null,
    );
  const last = asked.at(-1);
  return last?.status === "pending" ? last.id : null;
}

/**
 * The browser's conversation with the assistant: its stored turns, the turn streaming in, and the
 * mandate it waits on. A consent page answered elsewhere resumes the conversation by itself.
 */
export function useAssistant(walletId: string | undefined) {
  const {
    chatKey,
    chats,
    conversationId,
    setConversationId,
    cartId,
    setCartId,
    setChatKey,
    rememberChat,
    forgetChat,
  } = useShopper();
  const queryClient = useQueryClient();
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [problem, setProblem] = useState<null | string>(null);
  const answeredRef = useRef(new Set<string>());

  // The current chat as this browser remembers it; its key is what opens it.
  const current = chats.find((chat) => chat.id === conversationId) ?? null;
  const conversation = useQuery({
    ...conversationOptions({
      path: { conversation_id: conversationId ?? "" },
      headers: { "X-Conversation-Key": current?.key ?? "" },
    }),
    enabled: current !== null && chatKey !== null,
    retry: false,
  });

  // A conversation this browser has no key for, one the API refuses or no longer knows, or
  // one paid by another wallet, is forgotten.
  const stale =
    conversation.isError ||
    (conversationId !== null && current === null) ||
    (current !== null &&
      walletId !== undefined &&
      current.walletId !== walletId);
  const gone = conversation.isError ? conversationId : null;
  useEffect(() => {
    if (stale) setConversationId(null);
    if (gone !== null) forgetChat(gone);
  }, [stale, gone, setConversationId, forgetChat]);

  const send = useMutation({
    mutationFn: async (incoming: Incoming) => {
      if (walletId === undefined) throw new Error("no wallet to pay with");
      let chat = current;
      if (chat === null) {
        const created = await createConversation({
          body: { wallet_id: walletId, cart_id: cartId },
          throwOnError: true,
        });
        const { key, ...conversation } = created.data;
        chat = {
          id: conversation.id,
          key,
          walletId,
          title: requestFor(incoming).text.slice(0, CHAT_TITLE_LENGTH),
          at: conversation.created_at,
        };
        queryClient.setQueryData(
          conversationQueryKey({
            path: { conversation_id: chat.id },
            headers: { "X-Conversation-Key": key },
          }),
          conversation,
        );
        rememberChat(chat);
        setConversationId(chat.id);
      }
      const id = chat.id;
      const opened = { "X-Conversation-Key": chat.key };
      setProblem(null);
      setLive({ request: requestFor(incoming), events: [] });
      let failure: unknown = null;
      const { stream } = await sendMessage({
        path: { conversation_id: id },
        headers: opened,
        body: incoming,
        // A retry would send the message again; the shopper decides whether to.
        sseMaxRetryAttempts: 1,
        onSseError: (error) => {
          failure = error;
        },
      });
      let finished = false;
      let bag = cartId;
      for await (const event of stream) {
        switch (event.type) {
          case "done": {
            finished = true;
            const turn = event.turn;
            queryClient.setQueryData(
              conversationQueryKey({
                path: { conversation_id: id },
                headers: opened,
              }),
              (stored: Conversation | undefined) =>
                stored && {
                  ...stored,
                  cart_id: event.cart_id,
                  turns: [...stored.turns, turn],
                },
            );
            break;
          }
          case "error": {
            finished = true;
            setProblem(event.detail);
            break;
          }
          case "text":
          case "tool_call":
          case "tool_result": {
            setLive(
              (current) =>
                current && {
                  ...current,
                  events: appendEvent(current.events, event),
                },
            );
            if (event.type === "tool_result" && event.attachment !== null) {
              bag = await adoptAttachment(event.attachment, bag);
            }
          }
        }
      }
      if (!finished) {
        const detail = problemFor(failure);
        if (detail === PROBLEMS[401]) setChatKey(null);
        if (detail === PROBLEMS[404]) setConversationId(null);
        setProblem(detail);
      }
      setLive(null);
    },
  });

  /**
   * What a tool changed that the rest of the shop shows: the bag's cart, or an order that spent
   * it. The cart a turn adopts is passed along within the turn, because an order can follow the
   * cart in the same stream, before the stored id has caught up.
   */
  const adoptAttachment = async (
    attachment: NonNullable<ToolResultEvent["attachment"]>,
    bag: null | string,
  ): Promise<null | string> => {
    if (attachment.type === "cart") {
      setCartId(attachment.cart.id);
      await queryClient.invalidateQueries({
        queryKey: cartQueryKey({ path: { cart_id: attachment.cart.id } }),
      });
      return attachment.cart.id;
    }
    if (attachment.type === "order" && bag !== null) {
      await queryClient.invalidateQueries({
        queryKey: cartQueryKey({ path: { cart_id: bag } }),
      });
    }
    return bag;
  };

  const { mutate: sendIncoming, isPending: busy } = send;
  const turns = stale ? [] : (conversation.data?.turns ?? []);
  const awaited = awaitedMandate(turns, live);
  const awaitedMandateQuery = useQuery({
    ...mandateOptions({ path: { mandate_id: awaited ?? "" } }),
    enabled: awaited !== null && !busy,
    refetchInterval: (query) =>
      query.state.data?.status === "pending"
        ? PENDING_MANDATE_REFETCH_MS
        : false,
  });
  // The consent page answered: the agent is told once, and carries on from the answer.
  const settled = awaitedMandateQuery.data;
  useEffect(() => {
    if (
      settled === undefined ||
      settled.status === "pending" ||
      busy ||
      answeredRef.current.has(settled.id)
    ) {
      return;
    }
    answeredRef.current.add(settled.id);
    sendIncoming({ type: "mandate", mandate_id: settled.id });
  }, [settled, busy, sendIncoming]);

  return {
    conversationId: stale ? null : conversationId,
    turns,
    live,
    problem,
    busy,
    awaited,
    send: (text: string) => {
      sendIncoming({ type: "message", text });
    },
    /** Leave the current chat where it is and start the next message in a new one. */
    reset: () => {
      setProblem(null);
      setLive(null);
      setConversationId(null);
    },
    /** Open one of this browser's past chats. */
    open: (id: string) => {
      setProblem(null);
      setLive(null);
      setConversationId(id);
    },
  };
}
