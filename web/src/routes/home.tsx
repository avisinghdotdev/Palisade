import { useQuery } from "@tanstack/react-query";

import { healthOptions } from "../api/client/@tanstack/react-query.gen";

export default function Home() {
  const health = useQuery(healthOptions());

  if (!health.data) {
    return (
      <p>
        Palisade {__APP_VERSION__}
        {health.isError && ", API unreachable"}
      </p>
    );
  }
  return (
    <p>
      Palisade {__APP_VERSION__}, API {health.data.status} {health.data.version}
    </p>
  );
}
