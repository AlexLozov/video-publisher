export interface Health {
  status: string;
  version: string;
}

/** Platform names are translated on the client via the `platform.<id>` key. */
export interface PlatformInfo {
  id: string;
  available: boolean;
}

/**
 * Backend errors carry a machine-readable code (translated via `errors.<code>`)
 * instead of user-facing text.
 */
export class ApiError extends Error {
  constructor(
    readonly path: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(`${path}: HTTP ${status} (${code})`);
    this.name = "ApiError";
  }
}

async function readErrorCode(resp: Response): Promise<string> {
  try {
    const body: unknown = await resp.json();
    if (body && typeof body === "object" && "code" in body && typeof body.code === "string") {
      return body.code;
    }
  } catch {
    // not JSON
  }
  return "unknown";
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) {
    throw new ApiError(path, resp.status, await readErrorCode(resp));
  }
  return (await resp.json()) as T;
}

export const fetchHealth = () => getJson<Health>("/api/health");
export const fetchPlatforms = () => getJson<PlatformInfo[]>("/api/platforms");
