import { index, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("shop", "routes/shop.tsx"),
  route("credits", "routes/credits.tsx"),
  route("merchant", "routes/merchant.tsx"),
] satisfies RouteConfig;
