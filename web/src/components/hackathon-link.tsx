/** The hackathon's announcement page, in a new tab. */
export function HackathonLink() {
  return (
    <a
      className="underline underline-offset-2 hover:text-foreground"
      href="https://developer.paypal.com/community/blog/PayPal_AI_Hackathon/"
      rel="noreferrer"
      target="_blank"
    >
      PayPal AI Hackathon 2026
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}
