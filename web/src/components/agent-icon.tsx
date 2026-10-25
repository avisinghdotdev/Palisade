/** A shopping agent's robot head, drawn in the current text colour with a soft fill. */
export function AgentIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden className={className} fill="none" viewBox="0 0 24 24">
      <rect
        fill="currentColor"
        height="12.76"
        opacity=".2"
        rx="3"
        width="16.4"
        x="3.8"
        y="7.11"
      />
      <path
        d="M9.3 11.48v2.64m5.65-2.64v2.64m5.25-.39h2.12m-20.65 0H3.8m8.5-6.62c0-1.35 0-2.03-.32-2.52a2 2 0 0 0-.61-.6c-.5-.32-1.17-.32-2.52-.32h-.21"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
      <rect
        height="12.76"
        rx="3"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
        width="16.4"
        x="3.8"
        y="7.11"
      />
    </svg>
  );
}
