import { describe, expect, it } from "vitest";

import { cn, formatBytes } from "@/lib/utils";

describe("utils", () => {
  it("cn skips falsy values", () => {
    expect(cn("a", false, null, "b")).toBe("a b");
  });

  it.each([
    [512, "512 B"],
    [1536, "1.5 KB"],
    [5 * 1024 * 1024, "5.0 MB"],
  ])("formatBytes(%i) = %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});
