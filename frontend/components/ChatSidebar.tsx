"use client";

import { useState, useCallback } from "react";
import type { Conversation } from "@/lib/api";
import DeleteConfirmation from "./DeleteConfirmation";
import InlineRename from "./InlineRename";

interface ChatSidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));

  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export default function ChatSidebar({
  conversations,
  activeId,
  onSelect,
  onNewChat,
  onRename,
  onDelete,
  collapsed,
  onToggleCollapse,
}: ChatSidebarProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [collapsingId, setCollapsingId] = useState<string | null>(null);

  const handleDelete = useCallback(
    (id: string) => {
      setCollapsingId(id);
      setDeletingId(null);
      // Wait for collapse animation
      setTimeout(() => {
        onDelete(id);
        setCollapsingId(null);
      }, 250);
    },
    [onDelete]
  );

  return (
    <>
      {/* Mobile overlay */}
      {!collapsed && (
        <div
          className="fixed inset-0 bg-black/50 z-30 lg:hidden"
          onClick={onToggleCollapse}
        />
      )}

      <aside
        className={`
          fixed lg:static inset-y-0 left-0 z-40
          flex flex-col bg-bg-secondary border-r border-border
          transition-all duration-200 ease-out
          ${collapsed ? "-translate-x-full lg:translate-x-0" : ""} w-72
        `}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 h-14 border-b border-border shrink-0">
          <span className="text-sm font-semibold text-text-primary tracking-tight">
            Zane
          </span>
          <button
            onClick={onToggleCollapse}
            className="text-text-muted hover:text-text-secondary transition-colors lg:hidden"
            aria-label="Close sidebar"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* New Chat button */}
        <div className="px-3 pt-3 pb-2 shrink-0">
          <button
            onClick={onNewChat}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm text-text-primary hover:bg-bg-hover hover:border-text-muted transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            New Chat
          </button>
        </div>

        {/* Conversation list */}
        <nav className="flex-1 overflow-y-auto px-2 pb-4">
          {conversations.length === 0 && (
            <p className="text-xs text-text-muted text-center mt-8 px-4">
              No conversations yet. Start a new chat to begin sourcing components.
            </p>
          )}

          {conversations.map((conv) => {
            const isActive = conv.id === activeId;
            const isRenaming = renamingId === conv.id;
            const isDeleting = deletingId === conv.id;
            const isCollapsing = collapsingId === conv.id;

            return (
              <div
                key={conv.id}
                className={`
                  group relative rounded-lg mb-0.5 transition-colors
                  ${isActive ? "bg-bg-hover" : "hover:bg-bg-hover/50"}
                  ${isCollapsing ? "animate-collapse" : ""}
                `}
              >
                <button
                  onClick={() => {
                    if (!isRenaming) onSelect(conv.id);
                  }}
                  className="w-full text-left px-3 py-2.5 rounded-lg"
                >
                  {/* Title row */}
                  <div className="flex items-center gap-2">
                    {/* Running spinner */}
                    {conv.agent_status === "running" && (
                      <div className="shrink-0 w-3.5 h-3.5 border-2 border-accent/30 border-t-accent rounded-full animate-spin-slow" />
                    )}

                    {isRenaming ? (
                      <InlineRename
                        value={conv.title || "New Chat"}
                        onSave={(title) => {
                          onRename(conv.id, title);
                          setRenamingId(null);
                        }}
                        onCancel={() => setRenamingId(null)}
                      />
                    ) : (
                      <span className="text-sm text-text-primary truncate flex-1">
                        {conv.title || "New Chat"}
                      </span>
                    )}
                  </div>

                  {/* Date */}
                  <p className="text-[10px] text-text-muted mt-0.5">
                    {formatDate(conv.updated_at)}
                  </p>
                </button>

                {/* Action buttons on hover */}
                {!isRenaming && (
                  <div className="absolute right-2 top-2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    {/* Rename */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setRenamingId(conv.id);
                        setDeletingId(null);
                      }}
                      className="p-1 rounded text-text-muted hover:text-text-secondary hover:bg-bg-tertiary transition-colors"
                      title="Rename"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                      </svg>
                    </button>

                    {/* Delete */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeletingId(isDeleting ? null : conv.id);
                      }}
                      className="p-1 rounded text-text-muted hover:text-error hover:bg-error/10 transition-colors"
                      title="Delete"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                )}

                {/* Delete confirmation */}
                {isDeleting && (
                  <DeleteConfirmation
                    onConfirm={() => handleDelete(conv.id)}
                    onCancel={() => setDeletingId(null)}
                  />
                )}
              </div>
            );
          })}
        </nav>
      </aside>
    </>
  );
}
