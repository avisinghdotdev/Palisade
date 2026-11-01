import { CopyButton } from "@/components/copy-button";

/** A value with its name above it and a copy button beside it, as the console's side panels list them. */
export function DetailField({
  label,
  value,
  note,
  mono = true,
}: {
  label: string;
  value: null | string;
  note?: string;
  mono?: boolean;
}) {
  return (
    <div className="py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 flex min-h-6 items-center gap-1">
        {value === null ? (
          <span className="text-sm text-muted-foreground">None</span>
        ) : (
          <>
            <span className={`text-sm break-all ${mono ? "font-mono" : ""}`}>
              {value}
            </span>
            <CopyButton label={label} value={value} />
          </>
        )}
      </dd>
      {note !== undefined && (
        <dd className="text-xs text-muted-foreground">{note}</dd>
      )}
    </div>
  );
}
