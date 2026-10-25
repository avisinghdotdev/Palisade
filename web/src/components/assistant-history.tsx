import { MessageSquare } from "lucide-react";

import { formatLocal } from "@/money";
import { useShopper } from "@/shopper";

/** This browser's past chats, newest first; picking one reopens it where it left off. */
export function AssistantHistory({
  currentId,
  onPick,
}: {
  currentId: null | string;
  onPick: (id: string) => void;
}) {
  const { chats } = useShopper();

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        Past chats
      </h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Remembered in this browser only.
      </p>
      {chats.length === 0 ? (
        <p className="mt-6 text-sm text-muted-foreground">No chats yet.</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-1.5">
          {chats.map((chat) => (
            <li key={chat.id}>
              <button
                aria-current={chat.id === currentId ? "true" : undefined}
                className="flex w-full items-start gap-2.5 rounded-lg border bg-card px-3 py-2.5 text-left text-sm transition-colors hover:bg-secondary aria-[current=true]:border-primary/40 aria-[current=true]:bg-secondary/60 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                onClick={() => {
                  onPick(chat.id);
                }}
                type="button"
              >
                <MessageSquare
                  aria-hidden
                  className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                />
                <span className="min-w-0 flex-1">
                  <span className="line-clamp-2">{chat.title}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {formatLocal(chat.at)}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
