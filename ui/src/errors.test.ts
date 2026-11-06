import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { errorKey } from "./errors";

describe("errorKey", () => {
  it("maps API errors to errors.<code>", () => {
    expect(errorKey(new ApiError("/x", 503, "secret_store_unavailable"))).toBe(
      "errors.secret_store_unavailable",
    );
  });

  it("maps fetch network failures to errors.network", () => {
    expect(errorKey(new TypeError("Failed to fetch"))).toBe("errors.network");
  });

  it("maps anything else to errors.unknown", () => {
    expect(errorKey(new Error("boom"))).toBe("errors.unknown");
    expect(errorKey("string")).toBe("errors.unknown");
  });
});

describe("ApiError.fields", () => {
  it("strips the body. prefix", () => {
    const err = new ApiError("/x", 422, "validation_error", {
      fields: { "body.telegram.channel": "invalid_channel", "path.n": "int_parsing" },
    });
    expect(err.fields).toEqual({ "telegram.channel": "invalid_channel", "path.n": "int_parsing" });
  });

  it("is empty when params have no fields", () => {
    expect(new ApiError("/x", 400, "x").fields).toEqual({});
    expect(new ApiError("/x", 400, "x", { fields: "oops" }).fields).toEqual({});
  });
});
