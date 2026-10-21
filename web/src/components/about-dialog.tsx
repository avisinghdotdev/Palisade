import type { ReactNode } from "react";

import { useRef } from "react";

import { AboutIcon } from "@/components/about-icon";
import { HackathonLink } from "@/components/hackathon-link";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/** The "i" button every surface carries: what this page is, and that it is a hackathon entry. */
export function AboutDialog({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  // The focus that returns from the closed dialog would open the tooltip and leave it up. The
  // flag covers only that focus, which Radix moves in the same task as the close event.
  const closingRef = useRef(false);

  return (
    <Dialog>
      <Tooltip>
        <TooltipTrigger asChild>
          <DialogTrigger asChild>
            <Button
              aria-label={title}
              onFocus={(event) => {
                if (closingRef.current) event.preventDefault();
              }}
              size="icon-sm"
              variant="ghost"
            >
              <AboutIcon className="size-5" />
            </Button>
          </DialogTrigger>
        </TooltipTrigger>
        <TooltipContent>{title}</TooltipContent>
      </Tooltip>
      <DialogContent
        className="sm:max-w-md"
        onCloseAutoFocus={() => {
          closingRef.current = true;
          queueMicrotask(() => {
            closingRef.current = false;
          });
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Built for the <HackathonLink />. Everything runs in the PayPal
            sandbox with test money.
          </DialogDescription>
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}
