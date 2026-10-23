/** A browser tab title: the page, then the part of the demo it belongs to, as in "Orders | Console". */
export function pageTitle(page: string, area: "Console" | "Shop"): string {
  return `${page} | ${area}`;
}
