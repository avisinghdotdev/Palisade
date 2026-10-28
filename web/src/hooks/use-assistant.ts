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

import { statusOf } from "@/api-errors";
import { createConversation, sendMessage } from "@/api/client";
import {
  cartQueryKey,
  conversationOptions,
  conversationQueryKey,
  mandateOptions,
} from "@/api/client/@tanstack/react-query.gen";
import { client } from "@/api/client/client.gen";
import { errorDetail, storedChatKey, useShopper } from "@/shopper";

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
/** How often the panel reads a conversation the API is still answering in, unseen. */
const BUSY_REFETCH_MS = 2_000;
const CHAT_TITLE_LENGTH = 80;

const PROBLEMS: Record<number, string> = {
  401: "The access code no longer works. Enter it again.",
  403: "This browser can no longer open that chat. Start a new one.",
  404: "This conversation is gone. Start a new one.",
  409: "The assistant is still answering. Give it a moment.",
};
const DROPPED =
  "The connection dropped. The assistant may still be working; its answer shows here when it is done.";
const UNREACHABLE =
  "The store could not be reached. Check your connection and send again.";

/** A message the API refused before the turn began, so the shopper's words were not taken. */
class NotSent extends Error {}

/** The status the streaming client names when the API refused the request; undefined when the stream broke. */
function streamStatus(error: unknown): number | undefined {
  const match = /^SSE failed: (\d+)/.exec(
    error instanceof Error ? error.message : "",
  );
  return match === null ? undefined : Number(match[1]);
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
    // A refusal is final; a failed read is tried again.
    retry: (failures, error) => statusOf(error) === undefined && failures < 3,
    // A turn the panel lost sight of (a dropped stream, a reload) runs on in the API.
    refetchInterval: (query) =>
      query.state.data?.busy === true ? BUSY_REFETCH_MS : false,
  });

  // A conversation the API refuses or no longer knows is forgotten, with its key; one this
  // browser has no key for, or one paid by another wallet, is left. A wrong access code asks
  // for the code again and forgets nothing, and a failed read waits for the next.
  const readStatus = statusOf(conversation.error);
  const gone = readStatus === 403 || readStatus === 404 ? conversationId : null;
  const stale =
    gone !== null ||
    (conversationId !== null && current === null) ||
    (current !== null &&
      walletId !== undefined &&
      current.walletId !== walletId);
  useEffect(() => {
    if (stale) setConversationId(null);
    if (gone !== null) forgetChat(gone);
    if (readStatus === 401) setChatKey(null);
  }, [stale, gone, readStatus, setConversationId, forgetChat, setChatKey]);

  const send = useMutation({
    mutationFn: async (incoming: Incoming) => {
      if (walletId === undefined) throw new NotSent("No wallet to pay with.");
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
                  busy: false,
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
      setLive(null);
      if (finished) return;
      const status = streamStatus(failure);
      if (status === undefined) {
        // The turn runs on in the API and is stored when it ends; reading the chat shows it.
        setProblem(DROPPED);
        await queryClient.invalidateQueries({
          queryKey: conversationQueryKey({
            path: { conversation_id: id },
            headers: opened,
          }),
        });
        return;
      }
      if (status === 401) setChatKey(null);
      if (status === 403 || status === 404) {
        forgetChat(id);
        setConversationId(null);
      }
      throw new NotSent(PROBLEMS[status] ?? errorDetail(failure));
    },
    onError: (error) => {
      setLive(null);
      setProblem(
        error instanceof NotSent
          ? error.message
          : statusOf(error) === undefined
            ? UNREACHABLE
            : errorDetail(error),
      );
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

  const { mutate: sendIncoming, isPending: sending } = send;
  const turns = stale ? [] : (conversation.data?.turns ?? []);
  // Busy while this panel streams a turn, and while the API answers one the panel lost sight of.
  const busy = sending || (!stale && conversation.data?.busy === true);
  const awaited = awaitedMandate(turns, live);
  const awaitedMandateQuery = useQuery({
    ...mandateOptions({ path: { mandate_id: awaited ?? "" } }),
    enabled: awaited !== null && !busy,
    refetchInterval: (query) =>
      query.state.data?.status === "pending"
        ? PENDING_MANDATE_REFETCH_MS
        : false,
  });
  // The consent page answered: the agent is told once, and carries on from the answer. An
  // answer the API did not take is told again once the chat is free.
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
    sendIncoming(
      { type: "mandate", mandate_id: settled.id },
      {
        onError: () => {
          answeredRef.current.delete(settled.id);
        },
      },
    );
  }, [settled, busy, sendIncoming]);

  return {
    conversationId: stale ? null : conversationId,
    turns,
    live,
    // A dropped stream's note stands only until the API has finished the turn it was about.
    problem:
      problem === DROPPED && conversation.data?.busy === false ? null : problem,
    busy,
    awaited,
    /** Send the shopper's words with the bag they see now; `onNotSent` runs when they were not taken. */
    send: (text: string, onNotSent: () => void) => {
      if (busy) return;
      sendIncoming(
        { type: "message", text, cart_id: cartId },
        { onError: onNotSent },
      );
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
