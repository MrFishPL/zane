"use client";

import { useState, useRef, useEffect } from "react";

interface InlineRenameProps {
  value: string;
  onSave: (newValue: string) => void;
  onCancel: () => void;
}

export default function InlineRename({
  value,
  onSave,
  onCancel,
}: InlineRenameProps) {
  const [text, setText] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  function handleSubmit() {
    const trimmed = text.trim();
    if (trimmed && trimmed !== value) {
      onSave(trimmed);
    } else {
      onCancel();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === "Escape") {
      onCancel();
    }
  }

  return (
    <input
      ref={inputRef}
      type="text"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={handleSubmit}
      onKeyDown={handleKeyDown}
      className="w-full bg-bg-primary border border-accent rounded px-2 py-0.5 text-sm text-text-primary outline-none"
    />
  );
}
