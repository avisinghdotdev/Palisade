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
  route("shop/approve/:mandateId", "routes/shop-approve.tsx"),
  route("credits", "routes/credits.tsx"),
  layout("routes/console.tsx", [
    route("console", "routes/console-orders.tsx"),
    route("console/activity", "routes/console-activity.tsx"),
    route("console/orders/:number", "routes/console-order.tsx"),
  ]),
] satisfies RouteConfig;
