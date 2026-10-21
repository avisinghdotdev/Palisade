/** The Ridgeline Running mark, drawn in the current text colour. */
export function ShopLogo({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      className={className}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="3.5"
      viewBox="0 0 48 48"
    >
      <path d="M36.333 29.34L42.5 40.021m-20.5 0L15.833 29.34h12.5c3.556 0 5.778-3.849 4-6.928L24 7.978L5.5 40.02" />
    </svg>
  );
}
