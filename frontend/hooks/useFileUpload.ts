"use client";

import { useState, useCallback } from "react";
import { uploadFile as apiUpload, type UploadResult } from "@/lib/api";

const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100 MB
const ALLOWED_TYPES = [
  "image/png",
  "image/jpeg",
  "image/webp",
];
const ALLOWED_EXTENSIONS = ["png", "jpg", "jpeg", "webp"];

export interface FileUploadEntry {
  id: string;
  file: File;
  progress: number; // 0-100
  path: string | null;
  uploadId: string | null;
  error: string | null;
}

export function useFileUpload() {
  const [uploads, setUploads] = useState<FileUploadEntry[]>([]);

  const isUploading = uploads.some(
    (u) => u.progress < 100 && !u.error
  );

  const completedPaths = uploads
    .filter((u) => u.path !== null)
    .map((u) => u.path as string);

  const validateFile = (file: File): string | null => {
    if (file.size > MAX_FILE_SIZE) {
      return `File too large (max 100 MB)`;
    }
    const ext = file.name.split(".").pop()?.toLowerCase() || "";
    if (
      !ALLOWED_TYPES.includes(file.type) &&
      !ALLOWED_EXTENSIONS.includes(ext)
    ) {
      return `Unsupported file type. Allowed: PNG, JPG, WEBP`;
    }
    return null;
  };

  const upload = useCallback(
    async (file: File, conversationId?: string) => {
      const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;

      const error = validateFile(file);
      if (error) {
        setUploads((prev) => [
          ...prev,
          { id, file, progress: 0, path: null, uploadId: null, error },
        ]);
        return;
      }

      setUploads((prev) => [
        ...prev,
        { id, file, progress: 10, path: null, uploadId: null, error: null },
      ]);

      try {
        // Simulate progress increments while waiting for upload
        const progressTimer = setInterval(() => {
          setUploads((prev) =>
            prev.map((u) =>
              u.id === id && u.progress < 90
                ? { ...u, progress: u.progress + 10 }
                : u
            )
          );
        }, 200);

        try {
          const result: UploadResult = await apiUpload(
            file,
            conversationId
          );

          setUploads((prev) =>
            prev.map((u) =>
              u.id === id
                ? {
                    ...u,
                    progress: 100,
                    path: result.path,
                    uploadId: result.upload_id,
                  }
                : u
            )
          );
        } finally {
          clearInterval(progressTimer);
        }
      } catch (err) {
        setUploads((prev) =>
          prev.map((u) =>
            u.id === id
              ? {
                  ...u,
                  error:
                    err instanceof Error
                      ? err.message
                      : "Upload failed",
                }
              : u
          )
        );
      }
    },
    []
  );

  const removeUpload = useCallback((id: string) => {
    setUploads((prev) => prev.filter((u) => u.id !== id));
  }, []);

  const clearUploads = useCallback(() => {
    setUploads([]);
  }, []);

  return {
    uploadFile: upload,
    uploads,
    isUploading,
    completedPaths,
    removeUpload,
    clearUploads,
  };
}
