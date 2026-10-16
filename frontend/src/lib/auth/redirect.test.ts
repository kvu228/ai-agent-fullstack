import { describe, expect, it } from "vitest";

import { safeRedirectPath } from "@/lib/auth/redirect";

describe("safeRedirectPath", () => {
  it("keeps same-site paths", () => {
    expect(safeRedirectPath("/documents?tab=1")).toBe("/documents?tab=1");
  });

  it.each([null, "", "https://evil.example", "//evil.example", "/\\evil.example", "javascript:x"])(
    "falls back to / for %j",
    (next) => {
      expect(safeRedirectPath(next)).toBe("/");
    },
  );
});
