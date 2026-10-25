import type { ToolCallEvent } from "@/api/client";

/** What the agent is doing, as the shopper and the merchant read it while a tool runs. */
export const TOOL_LABELS: Record<ToolCallEvent["tool"], string> = {
  search_products: "Searching the catalog",
  get_product: "Reading a shoe's details",
  set_cart_line: "Updating your cart",
  request_mandate: "Asking for your approval",
  check_mandate: "Checking your approval",
  checkout: "Paying with PayPal",
  get_order: "Checking the order",
};
