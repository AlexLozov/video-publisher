export interface Health {
  status: string;
  version: string;
}

export interface PlatformInfo {
  id: string;
  name: string;
  available: boolean;
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) {
    throw new Error(`${path}: HTTP ${resp.status}`);
  }
  return (await resp.json()) as T;
}

export const fetchHealth = () => getJson<Health>("/api/health");
export const fetchPlatforms = () => getJson<PlatformInfo[]>("/api/platforms");
