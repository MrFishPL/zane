"use client";

import type { Message } from "@/lib/api";
import { getFileUrl } from "@/lib/api";
import AttachmentPreview from "./AttachmentPreview";
import BOMTable from "./BOMTable";
import { useState } from "react";
import ImageLightbox from "./ImageLightbox";

interface MessageBubbleProps {
  message: Message;
}

// ---------------------------------------------------------------------------
// Parse assistant JSON content
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type JsonData = Record<string, any>;

interface AssistantContent {
  status?: string;
  message?: string;
  data?: JsonData;
}

function parseContent(content: string | Record<string, unknown>): AssistantContent | null {
  if (typeof content === "string") {
    try {
      return JSON.parse(content);
    } catch {
      return null;
    }
  }
  if (typeof content === "object" && content !== null) {
    return content as AssistantContent;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Sub-renderers for assistant message types
// ---------------------------------------------------------------------------

function RecommendationView({ data, messageTxt }: { data: JsonData; messageTxt: string }) {
  return (
    <div className="space-y-4">
      {messageTxt && (
        <p className="text-sm text-text-primary leading-relaxed">
          {messageTxt}
        </p>
      )}
      <BOMTable
        components={data.components || []}
        notSourced={data.not_sourced}
        summary={data.bom_summary}
        exportFiles={data.export_files}
      />
    </div>
  );
}

function ClarificationView({ data, messageTxt }: { data: JsonData; messageTxt: string }) {
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);

  const annotatedImage = data?.annotated_image
    ? getFileUrl(data.annotated_image.replace(/^minio:\/\//, ""))
    : null;

  return (
    <div className="space-y-3">
      {messageTxt && (
        <p className="text-sm text-text-primary leading-relaxed">
          {messageTxt}
        </p>
      )}

      {data?.questions && data.questions.length > 0 && (
        <div className="space-y-2">
          {data.questions.map((q: JsonData, idx: number) => (
            <div
              key={q.id || idx}
              className="flex gap-3 items-start bg-bg-tertiary/50 rounded-lg p-3"
            >
              <span className="shrink-0 w-6 h-6 rounded-full bg-accent/20 text-accent text-xs font-medium flex items-center justify-center">
                {idx + 1}
              </span>
              <div>
                <p className="text-sm text-text-primary">{q.question}</p>
                {q.default && (
                  <p className="text-xs text-text-muted mt-1 italic">
                    Suggested: {q.default}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {annotatedImage && (
        <>
          <button
            onClick={() => setLightboxSrc(annotatedImage)}
            className="rounded-lg overflow-hidden border border-border hover:border-accent transition-colors"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={annotatedImage}
              alt="Annotated schematic"
              className="max-w-[400px] max-h-[300px] object-contain"
            />
          </button>
          {lightboxSrc && (
            <ImageLightbox
              src={lightboxSrc}
              alt="Annotated schematic"
              onClose={() => setLightboxSrc(null)}
            />
          )}
        </>
      )}
    </div>
  );
}

function AnalysisView({ data, messageTxt }: { data: JsonData; messageTxt: string }) {
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      {messageTxt && (
        <p className="text-sm text-text-primary leading-relaxed">
          {messageTxt}
        </p>
      )}

      {/* Blocks */}
      {data?.blocks && data.blocks.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider">
            Identified Blocks
          </h4>
          {data.blocks.map((block: JsonData, i: number) => (
            <div
              key={i}
              className="bg-bg-tertiary/50 rounded-lg p-3"
            >
              <p className="text-sm font-medium text-text-primary">
                {block.name}
                {block.page && (
                  <span className="text-text-muted font-normal ml-2">
                    (page {block.page})
                  </span>
                )}
              </p>
              {block.components && (
                <p className="text-xs text-text-secondary mt-1">
                  {block.components.join(", ")}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Identified components */}
      {data?.identified_components && data.identified_components.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-2">
            Identified Components
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {data.identified_components.map((comp: string, i: number) => (
              <span
                key={i}
                className="text-xs px-2 py-1 rounded-md bg-accent/10 text-accent border border-accent/20 font-mono"
              >
                {comp}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Unclear areas */}
      {data?.unclear_areas && data.unclear_areas.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider">
            Unclear Areas
          </h4>
          {data.unclear_areas.map((area: JsonData, i: number) => {
            const imgUrl = area.annotated_image
              ? getFileUrl(area.annotated_image.replace(/^minio:\/\//, ""))
              : null;
            return (
              <div
                key={i}
                className="bg-warning/5 border border-warning/20 rounded-lg p-3"
              >
                <p className="text-sm text-text-primary">
                  {area.description}
                  {area.page && (
                    <span className="text-text-muted ml-2">
                      (page {area.page})
                    </span>
                  )}
                </p>
                {imgUrl && (
                  <button
                    onClick={() => setLightboxSrc(imgUrl)}
                    className="mt-2 rounded-lg overflow-hidden border border-border hover:border-accent transition-colors"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={imgUrl}
                      alt={area.description}
                      className="max-w-[300px] max-h-[200px] object-contain"
                    />
                  </button>
                )}
              </div>
            );
          })}
          {lightboxSrc && (
            <ImageLightbox
              src={lightboxSrc}
              alt="Unclear area"
              onClose={() => setLightboxSrc(null)}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MessageBubble
// ---------------------------------------------------------------------------

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  // User message
  if (isUser) {
    const text =
      typeof message.content === "string"
        ? message.content
        : JSON.stringify(message.content);

    return (
      <div className="flex justify-end animate-fade-in">
        <div className="max-w-[70%]">
          <div className="bg-accent/15 border border-accent/20 rounded-2xl rounded-br-md px-4 py-3">
            <p className="text-sm text-text-primary whitespace-pre-wrap">
              {text}
            </p>
          </div>
          <AttachmentPreview paths={message.attachments || []} />
          <p className="text-[10px] text-text-muted mt-1 text-right">
            {new Date(message.created_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        </div>
      </div>
    );
  }

  // Assistant message
  const parsed = parseContent(message.content);

  // Fallback: plain string assistant message
  if (!parsed) {
    const text =
      typeof message.content === "string"
        ? message.content
        : JSON.stringify(message.content, null, 2);

    return (
      <div className="flex justify-start animate-fade-in">
        <div className="max-w-[85%]">
          <div className="bg-bg-secondary border border-border rounded-2xl rounded-bl-md px-4 py-3">
            <p className="text-sm text-text-primary whitespace-pre-wrap">
              {text}
            </p>
          </div>
          <AttachmentPreview paths={message.attachments || []} />
          <p className="text-[10px] text-text-muted mt-1">
            {new Date(message.created_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        </div>
      </div>
    );
  }

  // Structured assistant response
  return (
    <div className="flex justify-start animate-fade-in">
      <div className="max-w-[90%] w-full">
        <div className="bg-bg-secondary border border-border rounded-2xl rounded-bl-md px-4 py-4">
          {parsed.status === "recommendation" && parsed.data ? (
            <RecommendationView
              data={parsed.data}
              messageTxt={parsed.message || ""}
            />
          ) : parsed.status === "needs_clarification" && parsed.data ? (
            <ClarificationView
              data={parsed.data}
              messageTxt={parsed.message || ""}
            />
          ) : parsed.status === "analysis" && parsed.data ? (
            <AnalysisView
              data={parsed.data}
              messageTxt={parsed.message || ""}
            />
          ) : (
            <p className="text-sm text-text-primary whitespace-pre-wrap">
              {parsed.message || JSON.stringify(parsed, null, 2)}
            </p>
          )}
        </div>
        <AttachmentPreview paths={message.attachments || []} />
        <p className="text-[10px] text-text-muted mt-1">
          {new Date(message.created_at).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </p>
      </div>
    </div>
  );
}
