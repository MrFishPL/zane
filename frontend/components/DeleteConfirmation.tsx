"use client";

import { useEffect, useRef } from "react";

interface DeleteConfirmationProps {
  onConfirm: () => void;
  onCancel: () => void;
}

export default function DeleteConfirmation({
  onConfirm,
  onCancel,
}: DeleteConfirmationProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onCancel();
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [onCancel]);

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 z-50 animate-fade-in"
    >
      <div className="bg-bg-tertiary border border-border rounded-lg shadow-xl p-3 min-w-[200px]">
        <p className="text-sm text-text-primary mb-3">
          Delete this conversation?
        </p>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs rounded-md bg-bg-secondary text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-3 py-1.5 text-xs rounded-md bg-error/20 text-error hover:bg-error/30 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}
