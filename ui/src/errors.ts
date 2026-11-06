import { ApiError } from "./api";

/** Translation key for any error thrown by the API client. */
export function errorKey(err: unknown): string {
  if (err instanceof ApiError) {
    return `errors.${err.code}`;
  }
  if (err instanceof TypeError) {
    return "errors.network"; // fetch() rejects with TypeError when the server is unreachable
  }
  return "errors.unknown";
}
