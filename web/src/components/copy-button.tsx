import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

/** A small button that copies `value` and confirms it in place for a moment; paper never shows it. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => {
      setCopied(false);
    }, 1500);
    return () => {
      clearTimeout(timer);
    };
  }, [copied]);

  return (
    <Button
      aria-label={copied ? `${label} copied` : `Copy ${label}`}
      className="text-muted-foreground hover:text-foreground print:hidden"
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
        });
      }}
      size="icon-xs"
      type="button"
      variant="ghost"
    >
      {copied ? (
        <Check aria-hidden className="text-emerald-600" />
      ) : (
        <Copy aria-hidden />
      )}
    </Button>
  );
}
