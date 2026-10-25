import { ArrowDown, ArrowRight, SendHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { Wallet } from "@/api/client";

import { AgentRow, AssistantTurn } from "@/components/assistant-turn";
import { ClaudeLogo } from "@/components/claude-logo";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { settledMandates, type useAssistant } from "@/hooks/use-assistant";
import { TOOL_LABELS } from "@/tool-labels";

const MESSAGE_LENGTH = 2000;
// Within this many pixels of the bottom counts as reading the latest, so the view keeps following.
const FOLLOW_SLACK_PX = 48;

/** Ways to start, one per flow: approve a cart, approve a budget up front, just ask. */
const SUGGESTIONS = [
  "Trail shoes for muddy weekend runs, US 10, under $150",
  "Shop Road shoes for me up to $400 without asking each time, US 9.5",
  "Compare the Hoka Mach X 2 with the Nike Free RN Flyknit",
];

/** The chat itself: the turns so far, the one streaming in, and the box to write in. */
export function AssistantChat({
  assistant,
  wallet,
}: {
  assistant: ReturnType<typeof useAssistant>;
  wallet: undefined | Wallet;
}) {
  const [draft, setDraft] = useState("");
  const [follow, setFollow] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { turns, live } = assistant;

  // The view follows new words until the shopper scrolls up to read; then it stays put.
  useEffect(() => {
    const box = scrollRef.current;
    if (follow && box) box.scrollTop = box.scrollHeight;
  }, [follow, turns, live]);

  const submit = () => {
    const text = draft.trim();
    if (text === "" || assistant.busy || wallet === undefined) return;
    assistant.send(text);
    setDraft("");
    setFollow(true);
  };

  const lastEvent = live?.events.at(-1);
  const status =
    live === null
      ? assistant.awaited !== null
        ? "Waiting for your approval on the consent page"
        : turns.length > 0
          ? "Response complete"
          : ""
      : lastEvent?.type === "tool_call"
        ? TOOL_LABELS[lastEvent.tool]
        : "The assistant is answering";
  const settled = settledMandates(turns);

  return (
    <>
      <p aria-atomic="true" className="sr-only" role="status">
        {status}
      </p>
      <div className="relative min-h-0 flex-1">
        <div
          className="h-full overflow-y-auto px-4 py-4"
          onScroll={(event) => {
            const box = event.currentTarget;
            setFollow(
              box.scrollHeight - box.scrollTop - box.clientHeight <
                FOLLOW_SLACK_PX,
            );
          }}
          ref={scrollRef}
        >
          {turns.length === 0 && live === null ? (
            <div className="flex h-full flex-col justify-end gap-4">
              <AgentRow>
                <p className="w-fit rounded-2xl rounded-tl-sm bg-secondary px-3 py-2 text-sm">
                  Hi, I&apos;m Ridgeline Assistant. Tell me what you run and
                  your US size. I find shoes, fill your bag, and never pay
                  before you approve.
                </p>
              </AgentRow>
              <div>
                <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  Try asking
                </p>
                <ul className="mt-2 flex flex-col gap-1.5">
                  {SUGGESTIONS.map((suggestion) => (
                    <li key={suggestion}>
                      <button
                        className="group flex w-full items-center gap-2 rounded-lg border border-dashed bg-secondary/40 px-3 py-2.5 text-left text-sm transition-colors hover:border-solid hover:bg-secondary focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50"
                        disabled={wallet === undefined}
                        onClick={() => {
                          assistant.send(suggestion);
                          setFollow(true);
                        }}
                        type="button"
                      >
                        <span className="flex-1">{suggestion}</span>
                        <ArrowRight
                          aria-hidden
                          className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                        />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <ol
              aria-label="Conversation"
              aria-live="off"
              className="grid gap-5"
              role="log"
            >
              {turns.map((turn) => (
                <AssistantTurn
                  awaited={assistant.awaited}
                  events={turn.events}
                  key={turn.position}
                  request={turn.request}
                  settled={settled}
                  streaming={false}
                />
              ))}
              {live !== null && (
                <AssistantTurn
                  awaited={assistant.awaited}
                  events={live.events}
                  key="live"
                  request={live.request}
                  settled={settled}
                  streaming
                />
              )}
            </ol>
          )}
        </div>
        {!follow && (
          <Button
            className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full shadow-md"
            onClick={() => {
              setFollow(true);
            }}
            size="sm"
            variant="outline"
          >
            <ArrowDown aria-hidden />
            Jump to latest
          </Button>
        )}
      </div>
      {assistant.problem !== null && (
        <p
          className="border-t bg-destructive/10 px-4 py-2 text-sm text-destructive"
          role="alert"
        >
          {assistant.problem}
        </p>
      )}
      <form
        className="border-t bg-secondary/60 p-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div className="flex items-end gap-2">
          <label className="sr-only" htmlFor="assistant-message">
            Your message
          </label>
          <Textarea
            className="max-h-48 min-h-20 bg-card py-2"
            disabled={wallet === undefined}
            id="assistant-message"
            maxLength={MESSAGE_LENGTH}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            placeholder={
              wallet === undefined
                ? "No PayPal wallet is linked to pay with."
                : "Ask about shoes, sizes, or your order…"
            }
            rows={3}
            value={draft}
          />
          <Button
            aria-label="Send"
            disabled={
              assistant.busy || draft.trim() === "" || wallet === undefined
            }
            size="icon"
            type="submit"
          >
            <SendHorizontal aria-hidden />
          </Button>
        </div>
        <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          <ClaudeLogo className="size-3.5" />
          Powered by Claude
        </p>
      </form>
    </>
  );
}
