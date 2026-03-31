"use client";

import { useState } from "react";
import { getFileUrl } from "@/lib/api";
import ImageLightbox from "./ImageLightbox";

interface AttachmentPreviewProps {
  paths: (string | { path?: string; filename?: string })[];
}

function resolvePath(item: string | { path?: string; filename?: string }): string {
  if (typeof item === "string") return item;
  return item.path || "";
}

function isImage(path: string): boolean {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  return ["png", "jpg", "jpeg", "webp"].includes(ext);
}

function isPdf(path: string): boolean {
  return path.toLowerCase().endsWith(".pdf");
}

function getFilename(path: string): string {
  return path.split("/").pop() || path;
}

export default function AttachmentPreview({
  paths,
}: AttachmentPreviewProps) {
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);

  if (!paths || paths.length === 0) return null;

  return (
    <>
      <div className="flex flex-wrap gap-2 mt-2">
        {paths.map((item, i) => {
          const path = resolvePath(item);
          if (!path) return null;
          const url = getFileUrl(path);

          if (isImage(path)) {
            return (
              <button
                key={i}
                onClick={() => setLightboxSrc(url)}
                className="group relative rounded-lg overflow-hidden border border-border hover:border-accent transition-colors"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={url}
                  alt={getFilename(path)}
                  className="w-[200px] h-[140px] object-cover"
                />
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors flex items-center justify-center">
                  <svg
                    className="w-6 h-6 text-white opacity-0 group-hover:opacity-100 transition-opacity"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7"
                    />
                  </svg>
                </div>
              </button>
            );
          }

          if (isPdf(path)) {
            return (
              <a
                key={i}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 px-4 py-3 rounded-lg border border-border bg-bg-tertiary hover:border-accent hover:bg-bg-hover transition-colors max-w-[240px]"
              >
                <svg
                  className="w-8 h-8 text-error/80 shrink-0"
                  fill="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zm-1 1.5L18.5 9H13V3.5zM6 20V4h5v7h7v9H6z" />
                  <path d="M8 14h2v1H9v1h1v1H8v-3zm3 0h1.5c.28 0 .5.22.5.5v2c0 .28-.22.5-.5.5H11v-3zm1 2.5v-2h-.5v2h.5zM14 14h2v.5h-1.5v.5H16v.5h-1.5v1.5H14v-3z" />
                </svg>
                <div className="min-w-0">
                  <p className="text-sm text-text-primary truncate">
                    {getFilename(path)}
                  </p>
                  <p className="text-xs text-text-muted">PDF</p>
                </div>
              </a>
            );
          }

          // Fallback: generic file
          return (
            <a
              key={i}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-bg-tertiary hover:border-accent transition-colors text-sm text-text-secondary hover:text-text-primary"
            >
              <svg
                className="w-4 h-4 shrink-0"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
              {getFilename(path)}
            </a>
          );
        })}
      </div>

      {lightboxSrc && (
        <ImageLightbox
          src={lightboxSrc}
          onClose={() => setLightboxSrc(null)}
        />
      )}
    </>
  );
}
