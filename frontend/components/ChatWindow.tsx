"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import type { Message, AgentStatus } from "@/lib/api";
import MessageBubble from "./MessageBubble";
import FileUpload from "./FileUpload";
import StatusIndicator from "./StatusIndicator";
import type { FileUploadEntry } from "@/hooks/useFileUpload";

interface ChatWindowProps {
  messages: Message[];
  agentStatus: AgentStatus;
  uploads: FileUploadEntry[];
  isUploading: boolean;
  completedPaths: string[];
  onSend: (text: string, attachments: string[]) => void;
  onUploadFiles: (files: File[]) => void;
  onRemoveUpload: (id: string) => void;
  onToggleSidebar: () => void;
  conversationTitle: string | null;
  conversationId?: string;
}

export default function ChatWindow({
  messages,
  agentStatus,
  uploads,
  isUploading,
  completedPaths,
  onSend,
  onUploadFiles,
  onRemoveUpload,
  onToggleSidebar,
  conversationTitle,
  conversationId,
}: ChatWindowProps) {
  const [inputText, setInputText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isAgentRunning = agentStatus.status === "running";
  const canSend =
    inputText.trim().length > 0 && !isUploading && !isAgentRunning;

  // Auto-scroll to bottom only if user is already near the bottom
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const isNearBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight < 150;
    if (isNearBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages.length]);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
    }
  }, [inputText]);

  const handleSend = useCallback(() => {
    if (!canSend) return;
    onSend(inputText.trim(), completedPaths);
    setInputText("");
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [canSend, inputText, completedPaths, onSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const hasMessages = messages.length > 0;

  return (
    <div className="flex-1 flex flex-col h-full min-w-0">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 h-14 border-b border-border shrink-0 bg-bg-primary">
        {/* Sidebar toggle (mobile) */}
        <button
          onClick={onToggleSidebar}
          className="text-text-muted hover:text-text-secondary transition-colors"
          aria-label="Toggle sidebar"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>

        <h1 className="text-sm font-medium text-text-primary truncate">
          {conversationTitle || "New Chat"}
        </h1>

        {isAgentRunning && (
          <span className="shrink-0 inline-flex items-center gap-1.5 text-xs text-accent">
            <span className="w-2 h-2 rounded-full bg-accent status-pulse" />
            Working
          </span>
        )}
      </div>

      {/* Messages area */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
        {!hasMessages ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center h-full px-4 text-center">
            <div className="w-14 h-14 rounded-2xl bg-bg-secondary border border-border flex items-center justify-center mb-4">
              <svg
                className="w-7 h-7 text-accent"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z"
                />
              </svg>
            </div>
            <h2 className="text-lg font-medium text-text-primary mb-1">
              Upload a schematic to get started
            </h2>
            <p className="text-sm text-text-secondary max-w-md">
              Upload a schematic (PDF, photo, or sketch) and describe your
              requirements. Zane will analyze it and source real, purchasable
              components.
            </p>
          </div>
        ) : (
          /* Message list */
          <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
            {messages.map((msg) => (
              <MessageBubble key={msg.id} message={msg} conversationId={conversationId} />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Status indicator */}
      <StatusIndicator
        status={agentStatus.current_status}
        agentStatus={agentStatus.status}
      />

      {/* Input area */}
      <div className="border-t border-border bg-bg-primary px-4 py-3 shrink-0">
        <div className="max-w-4xl mx-auto">
          {/* File uploads (above input) */}
          {uploads.length > 0 && (
            <div className="mb-3">
              <FileUpload
                uploads={uploads}
                onFiles={onUploadFiles}
                onRemove={onRemoveUpload}
              />
            </div>
          )}

          {/* Input row */}
          <div className="flex items-end gap-2">
            {/* File upload button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isAgentRunning}
              className="shrink-0 p-2 rounded-lg text-text-muted hover:text-text-secondary hover:bg-bg-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              title="Attach files"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
              </svg>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.webp"
              multiple
              onChange={(e) => {
                if (e.target.files) {
                  onUploadFiles(Array.from(e.target.files));
                  e.target.value = "";
                }
              }}
              className="hidden"
            />

            {/* Textarea */}
            <div className="flex-1 relative">
              <textarea
                ref={textareaRef}
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  isAgentRunning
                    ? "Waiting for agent to finish..."
                    : "Describe your requirements or ask a question..."
                }
                disabled={isAgentRunning}
                rows={1}
                className="w-full resize-none bg-bg-secondary border border-border rounded-xl px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>

            {/* Send button */}
            <button
              onClick={handleSend}
              disabled={!canSend}
              className="shrink-0 p-2 rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Send message"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
              </svg>
            </button>
          </div>

          {/* Agent error */}
          {agentStatus.status === "failed" && agentStatus.error && (
            <div className="mt-2 px-3 py-2 rounded-lg bg-error/10 border border-error/20 text-sm text-error animate-fade-in">
              {agentStatus.error}
            </div>
          )}

          <p className="text-[10px] text-text-muted mt-2 text-center">
            Press Enter to send, Shift+Enter for new line
          </p>
        </div>
      </div>
    </div>
  );
}
