import { describe, expect, it } from "vitest";

import { buildCaption, fileName, parseHashtags } from "./caption";

describe("parseHashtags", () => {
  it("splits on spaces and commas, strips #, removes duplicates and blanks", () => {
    expect(parseHashtags("#travel, shorts  ##travel,, #cats\n#")).toEqual([
      "travel",
      "shorts",
      "cats",
    ]);
  });

  it("returns an empty list for empty input", () => {
    expect(parseHashtags("   ")).toEqual([]);
  });
});

describe("buildCaption (mirrors the backend)", () => {
  it("joins title, description and hashtags with blank lines", () => {
    expect(buildCaption(" Title ", "Body ", ["a", "b"])).toBe("Title\n\nBody\n\n#a #b");
  });

  it("skips empty parts", () => {
    expect(buildCaption("", "", [])).toBe("");
    expect(buildCaption("", "Body", [])).toBe("Body");
    expect(buildCaption("T", "", ["x"])).toBe("T\n\n#x");
  });
});

describe("fileName", () => {
  it.each([
    ["C:\\Videos\\clip.mp4", "clip.mp4"],
    ["/home/user/clip.mov", "clip.mov"],
    ["clip.mp4", "clip.mp4"],
    ["C:\\Videos\\", "Videos"],
  ])("%s → %s", (path, name) => {
    expect(fileName(path)).toBe(name);
  });

  it("returns the input when there is no name", () => {
    expect(fileName("\\")).toBe("\\");
  });
});
