import { formatUtc } from "@/money";

export interface TimelineEntry {
  key: string;
  at: string;
  label: string;
  source: string;
  detail: null | string;
  // PayPal's codes and ids read better in a fixed-width face; people's words do not.
  code: boolean;
}

/** Entries in the order they happened; events at the same instant keep the order they came in. */
export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  const ordered = entries.toSorted(
    (a, b) => Date.parse(a.at) - Date.parse(b.at),
  );
  return (
    <ol className="relative ml-1.5 border-l border-border">
      {ordered.map((entry) => (
        <li className="relative pb-6 pl-6 last:pb-0" key={entry.key}>
          <span
            aria-hidden
            className="absolute top-1.5 -left-[5px] size-2.5 rounded-full border-2 border-card bg-primary ring-1 ring-primary/30"
          />
          <p className="flex flex-wrap items-baseline gap-x-3">
            <span className="font-medium">{entry.label}</span>
            <time
              className="text-sm text-muted-foreground tabular-nums"
              dateTime={entry.at}
            >
              {formatUtc(entry.at)}
            </time>
          </p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {entry.source}
            {entry.detail !== null && entry.detail !== "" && (
              <span className={entry.code ? "font-mono text-xs" : ""}>
                {" "}
                {entry.code ? entry.detail : `· ${entry.detail}`}
              </span>
            )}
          </p>
        </li>
      ))}
    </ol>
  );
}
