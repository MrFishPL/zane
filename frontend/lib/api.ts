const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Conversation {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  agent_status: "idle" | "running" | "completed" | "failed";
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string | Record<string, unknown>; // JSON for assistant, string for user
  attachments: (string | { path?: string; filename?: string })[];
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  agent_status: "idle" | "running" | "completed" | "failed";
  messages: Message[];
}

export interface AgentStatus {
  status: "running" | "completed" | "failed" | "idle";
  current_status: string | null;
  error: string | null;
}

export interface UploadResult {
  path: string;
  upload_id: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API error ${res.status}: ${body}`);
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

export async function createConversation(): Promise<Conversation> {
  return request<Conversation>("/api/conversations", { method: "POST" });
}

export async function getConversations(): Promise<Conversation[]> {
  return request<Conversation[]>("/api/conversations");
}

export async function getConversation(
  id: string
): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/api/conversations/${id}`);
}

export async function updateConversation(
  id: string,
  title: string
): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function deleteConversation(id: string): Promise<void> {
  return request<void>(`/api/conversations/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

export async function sendMessage(
  conversationId: string,
  message: string,
  attachments: string[]
): Promise<void> {
  const attachmentDicts = attachments.map((path) => ({ path }));
  return request<void>(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content: message, attachments: attachmentDicts.length > 0 ? attachmentDicts : undefined }),
  });
}

// ---------------------------------------------------------------------------
// Agent
// ---------------------------------------------------------------------------

export async function getAgentStatus(
  conversationId: string
): Promise<AgentStatus> {
  return request<AgentStatus>(
    `/api/conversations/${conversationId}/agent-status`
  );
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------

export async function uploadFile(
  file: File,
  conversationId?: string
): Promise<UploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  if (conversationId) {
    formData.append("conversation_id", conversationId);
  }

  const res = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Upload failed ${res.status}: ${body}`);
  }

  return res.json();
}

export function getFileUrl(path: string): string {
  return `${API_BASE}/api/files/${path}`;
}

// ---------------------------------------------------------------------------
// Decisions
// ---------------------------------------------------------------------------

export async function sendDecision(
  conversationId: string,
  taskId: string,
  decisionId: string,
  choice: string,
): Promise<void> {
  return request<void>(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      content: `Decision: ${choice}`,
      decision_id: decisionId,
      task_id: taskId,
      choice,
    }),
  });
}
