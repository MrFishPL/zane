# API reference

Base URL: `http://localhost:8000`

## REST endpoints

### Conversations

#### Create conversation

```
POST /api/conversations
```

**Request body** (optional):
```json
{
  "title": "Optional title"
}
```

**Response** `201 Created`:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "00000000-0000-0000-0000-000000000001",
  "title": null,
  "created_at": "2025-01-15T10:30:00+00:00",
  "updated_at": "2025-01-15T10:30:00+00:00"
}
```

#### List conversations

```
GET /api/conversations
```

Returns all conversations for the current user, including agent task status.

**Response** `200 OK`:
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "user_id": "00000000-0000-0000-0000-000000000001",
    "title": "LNA Component Sourcing",
    "created_at": "2025-01-15T10:30:00+00:00",
    "updated_at": "2025-01-15T10:31:00+00:00",
    "agent_status": "running",
    "agent_current_status": "Searching BFP740 in Nexar..."
  }
]
```

The `agent_status` field is one of: `null` (no tasks), `"running"`, `"completed"`, `"failed"`.

#### Get conversation with messages

```
GET /api/conversations/{conversation_id}
```

**Response** `200 OK`:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "00000000-0000-0000-0000-000000000001",
  "title": "LNA Component Sourcing",
  "created_at": "2025-01-15T10:30:00+00:00",
  "messages": [
    {
      "id": "msg-uuid-1",
      "conversation_id": "550e8400-...",
      "role": "user",
      "content": "Find components for this LNA schematic",
      "attachments": [{"path": "minio://uploads/.../schematic.pdf"}],
      "created_at": "2025-01-15T10:31:00+00:00"
    },
    {
      "id": "msg-uuid-2",
      "conversation_id": "550e8400-...",
      "role": "assistant",
      "content": {"status": "recommendation", "message": "...", "data": {}},
      "attachments": [],
      "created_at": "2025-01-15T10:35:00+00:00"
    }
  ]
}
```

**Error** `404 Not Found`:
```json
{"detail": "Conversation not found"}
```

#### Update conversation title

```
PATCH /api/conversations/{conversation_id}
```

**Request body**:
```json
{
  "title": "New title"
}
```

**Response** `200 OK`:
```json
{
  "id": "550e8400-...",
  "title": "New title",
  "updated_at": "2025-01-15T10:32:00+00:00"
}
```

#### Delete conversation

```
DELETE /api/conversations/{conversation_id}
```

Deletes the conversation, all its messages, agent tasks (via cascading delete), and all associated MinIO files across the `uploads`, `temp`, and `exports` buckets.

**Response** `204 No Content`

**Error** `404 Not Found`:
```json
{"detail": "Conversation not found"}
```

### Messages

#### Send message

```
POST /api/conversations/{conversation_id}/messages
```

Saves the user message and submits an agent task to the Redis queue. Returns immediately with `202 Accepted`.

**Request body**:
```json
{
  "content": "Find components for this LNA schematic",
  "attachments": [{"path": "minio://uploads/.../schematic.pdf"}],
  "upload_ids": ["uuid-of-staged-upload"]
}
```

- `attachments`: file paths already in MinIO (optional).
- `upload_ids`: IDs of files uploaded via `POST /api/upload` before the conversation existed. These staged files are moved to the conversation path before the message is saved.

**Response** `202 Accepted`:
```json
{
  "message": {
    "id": "msg-uuid",
    "conversation_id": "conv-uuid",
    "role": "user",
    "content": "Find components for this LNA schematic",
    "attachments": [{"path": "minio://uploads/.../schematic.pdf"}],
    "created_at": "2025-01-15T10:31:00+00:00"
  },
  "task_id": "task-uuid",
  "status": "accepted"
}
```

**Error** `409 Conflict` (agent already running):
```json
{"detail": "Agent is already processing a task for this conversation"}
```

#### Get agent status

```
GET /api/conversations/{conversation_id}/agent-status
```

**Response** `200 OK` (task running):
```json
{
  "task_id": "task-uuid",
  "status": "running",
  "current_status": "Analyzing page 3/6..."
}
```

**Response** `200 OK` (no active task):
```json
{
  "status": "idle",
  "current_status": null
}
```

### File upload

#### Upload file

```
POST /api/upload
Content-Type: multipart/form-data
```

**Form fields**:
- `file` (required): the file to upload.
- `conversation_id` (optional): if provided, uploads directly to the conversation path. Otherwise, uploads to a staging area.

**Constraints**:
- Maximum file size: 100 MB.
- Allowed MIME types: `application/pdf`, `image/png`, `image/jpeg`, `image/webp`.

**Response** `200 OK`:
```json
{
  "path": "minio://uploads/00000000-.../staging/upload-uuid/schematic.pdf",
  "upload_id": "upload-uuid",
  "filename": "schematic.pdf",
  "size": 2048576,
  "content_type": "application/pdf"
}
```

**Error** `415 Unsupported Media Type`:
```json
{"detail": "Unsupported file type: text/plain. Allowed: application/pdf, image/jpeg, image/png, image/webp"}
```

**Error** `413 Request Entity Too Large`:
```json
{"detail": "File too large: 150000000 bytes. Maximum: 104857600 bytes (100MB)"}
```

### File download

#### Serve file

```
GET /api/files/{bucket}/{object_path}
```

Downloads a file from MinIO. Images and PDFs are served inline (displayed in browser). CSV and ZIP files are served as attachments (downloaded).

**Examples**:
```
GET /api/files/uploads/00000000-.../conv-id/schematic.pdf
GET /api/files/exports/00000000-.../conv-id/bom_2025-01-15.csv
GET /api/files/temp/annotated/page1_annotated.png
```

**Response**: raw file bytes with appropriate `Content-Type` and `Content-Disposition` headers.

**Error** `400 Bad Request`:
```json
{"detail": "Invalid file path -- expected {bucket}/{path}"}
```

**Error** `404 Not Found`:
```json
{"detail": "File not found"}
```

### Health check

```
GET /health
```

**Response** `200 OK`:
```json
{"status": "ok"}
```

## WebSocket protocol

### Connection

```
WS /ws/conversations/{conversation_id}
```

### Behavior

1. On connect, the server sends the current agent task status (if a task is running):
   ```json
   {
     "type": "status",
     "task_id": "task-uuid",
     "status": "running",
     "current_status": "Analyzing page 3/6..."
   }
   ```

2. While the agent is processing, the server pushes status updates:
   ```json
   {"task_id": "uuid", "type": "status", "text": "Searching BFP740 in Nexar..."}
   ```

3. When the agent finishes, the server pushes the final result:
   ```json
   {"task_id": "uuid", "type": "result", "data": { ... }}
   ```

4. On error:
   ```json
   {"task_id": "uuid", "type": "error", "error": "Description of what went wrong"}
   ```

### Reconnection

The frontend should implement automatic reconnection with exponential backoff (max 5 retries). On each reconnect, the backend re-sends the current status from Supabase. If the agent finished while the WebSocket was disconnected, the result is available via `GET /api/conversations/{id}`.

### Client messages

The server does not expect client messages. The WebSocket connection is kept alive by reading from the client to detect disconnection.

## Error response format

All API errors follow the standard FastAPI error format:

```json
{
  "detail": "Human-readable error description"
}
```

HTTP status codes used:
- `400` -- invalid request (bad path, missing fields)
- `404` -- resource not found
- `409` -- conflict (agent already running in this conversation)
- `413` -- file too large
- `415` -- unsupported media type
- `422` -- validation error (Pydantic)
- `429` -- server busy (task queue full)
