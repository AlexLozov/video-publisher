import { useEffect, useState } from "react";

/** API exposed by desktop/bridge.py (DesktopApi) through pywebview. */
interface DesktopApi {
  pick_video: () => Promise<string | null>;
  /** Windows "Save as" + write; resolves to the saved path or null if cancelled. */
  save_csv?: (fileName: string, content: string) => Promise<string | null>;
}

declare global {
  interface Window {
    pywebview?: { api?: Partial<DesktopApi> };
  }
}

export const FILE_DROPPED_EVENT = "vp:file-dropped";

function currentApi(): DesktopApi | null {
  const api = window.pywebview?.api;
  return api && typeof api.pick_video === "function" ? (api as DesktopApi) : null;
}

/**
 * The native bridge, or null when running in a normal browser (dev mode).
 * pywebview injects its API asynchronously and fires `pywebviewready`.
 */
export function useDesktopApi(): DesktopApi | null {
  const [api, setApi] = useState<DesktopApi | null>(currentApi);

  useEffect(() => {
    if (api) return;
    const onReady = () => setApi(currentApi());
    window.addEventListener("pywebviewready", onReady);
    return () => window.removeEventListener("pywebviewready", onReady);
  }, [api]);

  return api;
}

/** Calls `onPath` with the full path of a file dropped onto the desktop window. */
export function useFileDrop(onPath: (path: string) => void) {
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<unknown>).detail;
      if (typeof detail === "string" && detail) onPath(detail);
    };
    window.addEventListener(FILE_DROPPED_EVENT, handler);
    return () => window.removeEventListener(FILE_DROPPED_EVENT, handler);
  }, [onPath]);
}
