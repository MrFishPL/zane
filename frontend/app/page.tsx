"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  type Conversation,
  type Message,
  type AgentStatus,
  getConversations,
  getConversation,
  createConversation,
  updateConversation,
  deleteConversation,
  sendMessage,
  getAgentStatus,
} from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useFileUpload } from "@/hooks/useFileUpload";
import ChatSidebar from "@/components/ChatSidebar";
import ChatWindow from "@/components/ChatWindow";

// ---------------------------------------------------------------------------
// Default agent status
// ---------------------------------------------------------------------------

const IDLE_STATUS: AgentStatus = {
  status: "idle",
  current_status: null,
  error: null,
};

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function Home() {
  // Conversations
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>(IDLE_STATUS);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  // When true, user clicked "New Chat" but hasn't sent a message yet
  const [pendingNewChat, setPendingNewChat] = useState(false);

  const activeConv = conversations.find((c) => c.id === activeId) ?? null;

  // File uploads
  const {
    uploadFile,
    uploads,
    isUploading,
    completedPaths,
    removeUpload,
    clearUploads,
  } = useFileUpload();

  // Track whether we've done the initial load
  const didInitRef = useRef(false);

  // -------------------------------------------------------------------------
  // Load conversations on mount
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (didInitRef.current) return;
    didInitRef.current = true;

    getConversations()
      .then((convs) => {
        const sorted = convs.sort(
          (a, b) =>
            new Date(b.updated_at).getTime() -
            new Date(a.updated_at).getTime()
        );
        setConversations(sorted);
        // Auto-select the first conversation if any
        if (sorted.length > 0) {
          setActiveId(sorted[0].id);
        }
      })
      .catch((err) => {
        console.error("Failed to load conversations:", err);
      });
  }, []);

  // -------------------------------------------------------------------------
  // Load messages when active conversation changes
  // -------------------------------------------------------------------------

  const loadConversation = useCallback(
    (id: string) => {
      getConversation(id)
        .then((detail) => {
          setMessages(detail.messages || []);
          // Check if there are assistant messages — if so, agent is done
          const hasAssistantMsg = (detail.messages || []).some(
            (m: Message) => m.role === "assistant"
          );
          if (hasAssistantMsg) {
            setAgentStatus(IDLE_STATUS);
          } else {
            // Check conversation status from the list
            const conv = conversations.find((c) => c.id === id);
            if (conv?.agent_status === "running") {
              setAgentStatus({ status: "running", current_status: null, error: null });
            } else {
              setAgentStatus(IDLE_STATUS);
            }
          }
        })
        .catch((err: unknown) => {
          console.error("Failed to load conversation:", err);
        });
    },
    [conversations]
  );

  useEffect(() => {
    if (!activeId || pendingNewChat) {
      return;
    }
    loadConversation(activeId);
  }, [activeId, pendingNewChat, loadConversation]);

  // -------------------------------------------------------------------------
  // Poll agent status for running conversations
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (!activeId || pendingNewChat) return;
    const conv = conversations.find((c) => c.id === activeId);
    if (conv?.agent_status !== "running") return;

    getAgentStatus(activeId)
      .then((status) => setAgentStatus(status))
      .catch(() => {});
  }, [activeId, conversations, pendingNewChat]);

  // -------------------------------------------------------------------------
  // WebSocket for real-time updates
  // -------------------------------------------------------------------------

  const wsConversationId =
    activeId && !pendingNewChat ? activeId : null;

  const handleWsMessage = useCallback(
    (raw: unknown) => {
      if (!raw || typeof raw !== "object") return;
      const data = raw as {
        type?: string;
        text?: string;
        current_status?: string;
        message?: Message;
        error?: string;
        title?: string;
        id?: string;
        role?: string;
      };

      // Status update from agent
      if (data.type === "status") {
        setAgentStatus((prev) => ({
          ...prev,
          status: "running",
          current_status: data.text || data.current_status || prev.current_status,
        }));
      }

      // Agent completed with result
      if (data.type === "result" || data.type === "message") {
        setAgentStatus(IDLE_STATUS);

        // Update conversation status in sidebar
        setConversations((prev) =>
          prev.map((c) =>
            c.id === activeId ? { ...c, agent_status: "completed" } : c
          )
        );

        // Re-fetch messages from the API to get the saved assistant response
        if (activeId) {
          getConversation(activeId)
            .then((detail) => setMessages(detail.messages || []))
            .catch(() => {});
        }
      }

      // Agent error
      if (data.type === "error") {
        setAgentStatus({
          status: "failed",
          current_status: null,
          error: data.error || "Agent encountered an error",
        });
        setConversations((prev) =>
          prev.map((c) =>
            c.id === activeId ? { ...c, agent_status: "failed" } : c
          )
        );
      }

      // Title update (auto-generated)
      if (data.type === "title_update") {
        setConversations((prev) =>
          prev.map((c) =>
            c.id === activeId ? { ...c, title: data.title ?? null } : c
          )
        );
      }
    },
    [activeId]
  );

  useWebSocket(wsConversationId, handleWsMessage);

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------

  const handleNewChat = useCallback(() => {
    setActiveId(null);
    setMessages([]);
    setAgentStatus(IDLE_STATUS);
    setPendingNewChat(true);
    clearUploads();
    // On mobile, close the sidebar
    setSidebarCollapsed(true);
  }, [clearUploads]);

  const handleSelectConversation = useCallback(
    (id: string) => {
      if (id === activeId) return;
      setActiveId(id);
      setPendingNewChat(false);
      clearUploads();
      // On mobile, close the sidebar
      setSidebarCollapsed(true);
    },
    [activeId, clearUploads]
  );

  const handleRename = useCallback(
    async (id: string, title: string) => {
      try {
        await updateConversation(id, title);
        setConversations((prev) =>
          prev.map((c) => (c.id === id ? { ...c, title } : c))
        );
      } catch (err) {
        console.error("Failed to rename conversation:", err);
      }
    },
    []
  );

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteConversation(id);
        setConversations((prev) => prev.filter((c) => c.id !== id));
        if (activeId === id) {
          setActiveId(null);
          setMessages([]);
          setAgentStatus(IDLE_STATUS);
        }
      } catch (err) {
        console.error("Failed to delete conversation:", err);
      }
    },
    [activeId]
  );

  const handleSend = useCallback(
    async (text: string, attachments: string[]) => {
      let convId = activeId;

      // If this is a new chat, create the conversation first
      if (pendingNewChat || !convId) {
        try {
          const newConv = await createConversation();
          convId = newConv.id;
          setActiveId(newConv.id);
          setPendingNewChat(false);
          setConversations((prev) => [newConv, ...prev]);
        } catch (err) {
          console.error("Failed to create conversation:", err);
          return;
        }
      }

      // Optimistically add user message
      const optimisticMsg: Message = {
        id: `temp-${Date.now()}`,
        role: "user",
        content: text,
        attachments,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimisticMsg]);

      // Clear uploads
      clearUploads();

      // Set agent status to running
      setAgentStatus({ status: "running", current_status: null, error: null });
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId ? { ...c, agent_status: "running" } : c
        )
      );

      try {
        await sendMessage(convId, text, attachments);
      } catch (err) {
        console.error("Failed to send message:", err);
        setAgentStatus({
          status: "failed",
          current_status: null,
          error:
            err instanceof Error ? err.message : "Failed to send message",
        });
      }
    },
    [activeId, pendingNewChat, clearUploads]
  );

  const handleUploadFiles = useCallback(
    (files: File[]) => {
      files.forEach((file) => {
        uploadFile(file, pendingNewChat ? undefined : activeId ?? undefined);
      });
    },
    [uploadFile, activeId, pendingNewChat]
  );

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="flex h-full">
      <ChatSidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={handleSelectConversation}
        onNewChat={handleNewChat}
        onRename={handleRename}
        onDelete={handleDelete}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
      />
      <ChatWindow
        messages={messages}
        agentStatus={agentStatus}
        uploads={uploads}
        isUploading={isUploading}
        completedPaths={completedPaths}
        onSend={handleSend}
        onUploadFiles={handleUploadFiles}
        onRemoveUpload={removeUpload}
        onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
        conversationTitle={activeConv?.title ?? null}
      />
    </div>
  );
}
