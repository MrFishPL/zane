"use client";

import { useRef, useState, useCallback } from "react";
import type { FileUploadEntry } from "@/hooks/useFileUpload";

interface FileUploadProps {
  uploads: FileUploadEntry[];
  onFiles: (files: File[]) => void;
  onRemove: (id: string) => void;
}

const ALLOWED_EXTENSIONS = ".pdf,.png,.jpg,.jpeg,.webp";

export default function FileUpload({
  uploads,
  onFiles,
  onRemove,
}: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList) return;
      const files = Array.from(fileList);
      onFiles(files);
    },
    [onFiles]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles]
  );

  const hasActiveUploads = uploads.length > 0;

  return (
    <div className="space-y-2">
      {/* Drop zone - only show when no uploads yet */}
      {!hasActiveUploads && (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={`
            border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors
            ${
              dragOver
                ? "border-accent bg-accent/5"
                : "border-border hover:border-text-muted"
            }
          `}
        >
          <svg
            className="w-6 h-6 mx-auto mb-1 text-text-muted"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
            />
          </svg>
          <p className="text-sm text-text-secondary">
            Drop files here or click to select
          </p>
          <p className="text-xs text-text-muted mt-1">
            PDF, PNG, JPG, WEBP up to 100 MB
          </p>
        </div>
      )}

      {/* File input (hidden) */}
      <input
        ref={inputRef}
        type="file"
        accept={ALLOWED_EXTENSIONS}
        multiple
        onChange={(e) => handleFiles(e.target.files)}
        className="hidden"
      />

      {/* Upload progress list */}
      {uploads.length > 0 && (
        <div className="space-y-1.5">
          {uploads.map((upload) => (
            <div
              key={upload.id}
              className="flex items-center gap-2 bg-bg-tertiary rounded-md px-3 py-2 animate-fade-in"
            >
              {/* File icon */}
              <svg
                className="w-4 h-4 shrink-0 text-text-muted"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"
                />
              </svg>

              {/* File name + progress */}
              <div className="flex-1 min-w-0">
                <p className="text-xs text-text-primary truncate">
                  {upload.file.name}
                </p>
                {upload.error ? (
                  <p className="text-xs text-error">{upload.error}</p>
                ) : upload.progress < 100 ? (
                  <div className="w-full bg-bg-primary rounded-full h-1 mt-1">
                    <div
                      className="bg-accent h-1 rounded-full transition-all duration-300"
                      style={{ width: `${upload.progress}%` }}
                    />
                  </div>
                ) : (
                  <p className="text-xs text-success">Uploaded</p>
                )}
              </div>

              {/* Remove button */}
              <button
                onClick={() => onRemove(upload.id)}
                className="text-text-muted hover:text-text-secondary transition-colors shrink-0"
                aria-label="Remove file"
              >
                <svg
                  className="w-3.5 h-3.5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
