import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FILE_DROPPED_EVENT, useDesktopApi, useFileDrop } from "./desktop";

function ApiProbe() {
  const api = useDesktopApi();
  return <span>{api ? "desktop" : "browser"}</span>;
}

function DropProbe({ onPath }: { onPath: (p: string) => void }) {
  useFileDrop(onPath);
  return null;
}

afterEach(() => {
  delete window.pywebview;
});

describe("useDesktopApi", () => {
  it("is null in a normal browser", () => {
    render(<ApiProbe />);
    expect(screen.getByText("browser")).toBeInTheDocument();
  });

  it("is available when pywebview is already injected", () => {
    window.pywebview = { api: { pick_video: vi.fn() } };
    render(<ApiProbe />);
    expect(screen.getByText("desktop")).toBeInTheDocument();
  });

  it("picks up the API after pywebviewready", () => {
    render(<ApiProbe />);
    act(() => {
      window.pywebview = { api: { pick_video: vi.fn() } };
      window.dispatchEvent(new Event("pywebviewready"));
    });
    expect(screen.getByText("desktop")).toBeInTheDocument();
  });

  it("ignores an API without pick_video", () => {
    window.pywebview = { api: {} };
    render(<ApiProbe />);
    act(() => {
      window.dispatchEvent(new Event("pywebviewready"));
    });
    expect(screen.getByText("browser")).toBeInTheDocument();
  });
});

describe("useFileDrop", () => {
  it("passes dropped paths and ignores empty or non-string details", () => {
    const onPath = vi.fn();
    const { unmount } = render(<DropProbe onPath={onPath} />);
    window.dispatchEvent(new CustomEvent(FILE_DROPPED_EVENT, { detail: "C:\\a.mp4" }));
    window.dispatchEvent(new CustomEvent(FILE_DROPPED_EVENT, { detail: "" }));
    window.dispatchEvent(new CustomEvent(FILE_DROPPED_EVENT, { detail: 42 }));
    expect(onPath).toHaveBeenCalledTimes(1);
    expect(onPath).toHaveBeenCalledWith("C:\\a.mp4");

    unmount();
    window.dispatchEvent(new CustomEvent(FILE_DROPPED_EVENT, { detail: "C:\\b.mp4" }));
    expect(onPath).toHaveBeenCalledTimes(1);
  });
});
