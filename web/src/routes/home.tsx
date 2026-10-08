import { useQuery } from "@tanstack/react-query";

import {
  healthOptions,
  paypalHealthOptions,
} from "../api/client/@tanstack/react-query.gen";

export default function Home() {
  const health = useQuery(healthOptions());
  const paypal = useQuery(paypalHealthOptions());

  return (
    <>
      <p>
        Palisade {__APP_VERSION__}
        {health.data && `, API ${health.data.status} ${health.data.version}`}
        {health.isError && ", API unreachable"}
      </p>
      <p>
        PayPal:{" "}
        {paypal.data
          ? "connected"
          : paypal.isError
            ? "unreachable"
            : "checking"}
      </p>
    </>
  );
}
