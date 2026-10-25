import { useMutation } from "@tanstack/react-query";
import { KeyRound, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { assistantAccess } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useShopper } from "@/shopper";

/** The code screen: the chat opens only with the key the team shared. */
export function AssistantAccess() {
  const { setChatKey } = useShopper();
  const [code, setCode] = useState("");
  const check = useMutation({
    mutationFn: async (candidate: string) => {
      // Sent by hand this once; the stored code, which the client sends by itself, follows a 204.
      await assistantAccess({
        headers: { "X-Chat-Key": candidate },
        throwOnError: true,
      });
      return candidate;
    },
    onSuccess: setChatKey,
  });

  return (
    <form
      className="flex flex-1 flex-col justify-center gap-4 px-6 py-8"
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = code.trim();
        if (trimmed !== "") check.mutate(trimmed);
      }}
    >
      <KeyRound aria-hidden className="size-8 text-muted-foreground" />
      <div>
        <h3 className="text-base font-semibold">Enter the Access Key</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Please enter the chat access key that was shared with you.
        </p>
      </div>
      <label className="sr-only" htmlFor="chat-access-code">
        Access key
      </label>
      <Input
        autoComplete="off"
        id="chat-access-code"
        onChange={(event) => {
          setCode(event.target.value);
          check.reset();
        }}
        placeholder="Access key"
        type="password"
        value={code}
      />
      {check.isError && (
        <p className="text-sm text-destructive" role="alert">
          That key is not right.
        </p>
      )}
      <Button disabled={check.isPending || code.trim() === ""} type="submit">
        {check.isPending && (
          <LoaderCircle aria-hidden className="motion-safe:animate-spin" />
        )}
        Open the Chat
      </Button>
    </form>
  );
}
