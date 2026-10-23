import {
  index,
  layout,
  route,
  type RouteConfig,
} from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("shop", "routes/shop.tsx"),
  route("shop/orders/:number", "routes/shop-order.tsx"),
  route("shop/wallet/:outcome", "routes/shop-wallet.tsx"),
  route("credits", "routes/credits.tsx"),
  layout("routes/console.tsx", [
    route("merchant", "routes/console-orders.tsx"),
    route("merchant/activity", "routes/console-activity.tsx"),
    route("merchant/orders/:number", "routes/console-order.tsx"),
  ]),
] satisfies RouteConfig;
