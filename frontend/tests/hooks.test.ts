/**
 * Basic tests for custom hooks.
 * These verify module exports and type correctness.
 */

describe("useWebSocket hook", () => {
  it("exports correctly", async () => {
    const mod = await import("@/hooks/useWebSocket");
    expect(mod.useWebSocket).toBeDefined();
    expect(typeof mod.useWebSocket).toBe("function");
  });
});

describe("useFileUpload hook", () => {
  it("exports correctly", async () => {
    const mod = await import("@/hooks/useFileUpload");
    expect(mod.useFileUpload).toBeDefined();
    expect(typeof mod.useFileUpload).toBe("function");
  });
});
