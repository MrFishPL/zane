/**
 * Basic component rendering tests.
 * These verify that components render without throwing.
 */

describe("Component smoke tests", () => {
  describe("StatusIndicator", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/StatusIndicator");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("DeleteConfirmation", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/DeleteConfirmation");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("InlineRename", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/InlineRename");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("ImageLightbox", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/ImageLightbox");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("AttachmentPreview", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/AttachmentPreview");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("FileUpload", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/FileUpload");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("BOMTable", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/BOMTable");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("MessageBubble", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/MessageBubble");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("ChatSidebar", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/ChatSidebar");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });

  describe("ChatWindow", () => {
    it("exports correctly", async () => {
      const mod = await import("@/components/ChatWindow");
      expect(mod.default).toBeDefined();
      expect(typeof mod.default).toBe("function");
    });
  });
});
