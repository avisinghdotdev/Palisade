import { client } from "@/api/client/client.gen";

// The client throws the API's error body, which says what went wrong but not the status. The
// status is what tells "this is gone" (404) from "try again" (a 5xx, or no answer at all), so
// it rides along on every refused request.
client.interceptors.error.use((error, response) =>
  response === undefined
    ? error
    : {
        ...(typeof error === "object" && error !== null ? error : {}),
        status: response.status,
      },
);

/** The HTTP status the API answered a refused request with; undefined when it never answered. */
export function statusOf(error: unknown): number | undefined {
  return typeof error === "object" &&
    error !== null &&
    "status" in error &&
    typeof error.status === "number"
    ? error.status
    : undefined;
}
