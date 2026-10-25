import { History, X } from "lucide-react";
import { Dialog } from "radix-ui";
import { useEffect, useState } from "react";

import type { Wallet } from "@/api/client";

import { assistantAccess } from "@/api/client";
import { AgentIcon } from "@/components/agent-icon";
import { AssistantAccess } from "@/components/assistant-access";
import { AssistantChat } from "@/components/assistant-chat";
import { AssistantHistory } from "@/components/assistant-history";
import { NewChatIcon } from "@/components/new-chat-icon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAssistant } from "@/hooks/use-assistant";
import { useHydrated } from "@/hooks/use-hydrated";
import { useMediaQuery } from "@/hooks/use-media-query";
import { useShopper } from "@/shopper";

/**
 * The in-page agent: a button in the corner shoppers expect, opening a window above it. On a
 * wide screen the window floats over the shop without taking it over, and its height stops
 * short of the pinned header; on a phone it fills the screen and takes the focus like any dialog.
 */
export function ShopAssistant({ wallet }: { wallet: undefined | Wallet }) {
  const { chatKey, setChatKey } = useShopper();
  const assistant = useAssistant(wallet?.id);
  const hydrated = useHydrated();
  const wide = useMediaQuery("(min-width: 640px)");
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"chat" | "history">("chat");

  // A stored key the team has since changed goes back to the key screen on opening.
  useEffect(() => {
    if (!open || chatKey === null) return;
    void assistantAccess().then(({ response }) => {
      if (response?.status === 401) setChatKey(null);
    });
  }, [open, chatKey, setChatKey]);

  const signedIn = hydrated && chatKey !== null;

  return (
    <Dialog.Root
      modal={!wide}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setView("chat");
      }}
      open={open}
    >
      <Dialog.Trigger asChild>
        <Button
          className="fixed right-4 bottom-4 z-40 h-11 rounded-full px-4 shadow-lg"
          size="lg"
        >
          <AgentIcon className="size-5" />
          {open ? "Close" : "Shop with the Agent"}
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        {!wide && (
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/10 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        )}
        <Dialog.Content
          aria-describedby={undefined}
          className="theme-shop fixed z-40 flex flex-col overflow-hidden bg-card text-foreground shadow-xl outline-none max-sm:inset-0 sm:right-4 sm:bottom-20 sm:h-[42rem] sm:max-h-[calc(100dvh-9.5rem)] sm:w-[25rem] sm:rounded-2xl sm:border data-open:animate-in data-open:fade-in-0 data-open:slide-in-from-bottom-4 data-closed:animate-out data-closed:fade-out-0"
          onInteractOutside={(event) => {
            // Beside the shop, a click on the shop is a click on the shop, not a close.
            if (wide) event.preventDefault();
          }}
        >
          <header className="flex items-center gap-2 border-b px-4 py-3">
            <AgentIcon className="size-5 shrink-0 text-primary" />
            <Dialog.Title className="truncate text-base font-semibold">
              Ridgeline Assistant
            </Dialog.Title>
            <Badge variant="secondary">AI agent</Badge>
            <div className="ml-auto flex shrink-0 items-center gap-0.5">
              {signedIn && (
                <>
                  <Button
                    aria-label="Past chats"
                    aria-pressed={view === "history"}
                    onClick={() => {
                      setView(view === "history" ? "chat" : "history");
                    }}
                    size="icon-sm"
                    variant="ghost"
                  >
                    <History aria-hidden />
                  </Button>
                  <Button
                    aria-label="New chat"
                    disabled={assistant.busy}
                    onClick={() => {
                      assistant.reset();
                      setView("chat");
                    }}
                    size="icon-sm"
                    variant="ghost"
                  >
                    <NewChatIcon className="size-4" />
                  </Button>
                </>
              )}
              <Dialog.Close asChild>
                <Button aria-label="Close" size="icon-sm" variant="ghost">
                  <X aria-hidden />
                </Button>
              </Dialog.Close>
            </div>
          </header>
          {!signedIn ? (
            <AssistantAccess />
          ) : view === "history" ? (
            <AssistantHistory
              currentId={assistant.conversationId}
              onPick={(id) => {
                assistant.open(id);
                setView("chat");
              }}
            />
          ) : (
            <AssistantChat assistant={assistant} wallet={wallet} />
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
