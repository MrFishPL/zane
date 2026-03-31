/**
 * Basic tests for the API client module.
 * These verify function signatures and URL construction.
 */

import { getFileUrl } from "@/lib/api";

describe("api", () => {
  describe("getFileUrl", () => {
    it("constructs a file URL from a path", () => {
      const url = getFileUrl("exports/user1/conv1/bom.csv");
      expect(url).toBe("http://localhost:8000/api/files/exports/user1/conv1/bom.csv");
    });

    it("handles paths with special characters", () => {
      const url = getFileUrl("uploads/user1/file%20name.pdf");
      expect(url).toContain("http://localhost:8000/api/files/");
      expect(url).toContain("file%20name.pdf");
    });
  });
});

describe("api module exports", () => {
  it("exports all expected functions", async () => {
    const api = await import("@/lib/api");

    expect(typeof api.createConversation).toBe("function");
    expect(typeof api.getConversations).toBe("function");
    expect(typeof api.getConversation).toBe("function");
    expect(typeof api.updateConversation).toBe("function");
    expect(typeof api.deleteConversation).toBe("function");
    expect(typeof api.sendMessage).toBe("function");
    expect(typeof api.getAgentStatus).toBe("function");
    expect(typeof api.uploadFile).toBe("function");
    expect(typeof api.getFileUrl).toBe("function");
  });
});
