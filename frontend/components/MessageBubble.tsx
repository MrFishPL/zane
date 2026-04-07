"use client";

import type { Message } from "@/lib/api";
import { getFileUrl, sendDecision } from "@/lib/api";
import AttachmentPreview from "./AttachmentPreview";
import BOMTable from "./BOMTable";
import { useState } from "react";
import ImageLightbox from "./ImageLightbox";

interface MessageBubbleProps {
  message: Message;
  conversationId?: string;
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
  decisions?: JsonData[];
  task_id?: string;
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
  // Map new orchestrator BOM format to BOMTable's expected format
  const rawComponents = data.components || data.bom || [];
  const components = rawComponents.map((c: JsonData) => ({
    ref: c.ref || "",
    mpn: c.mpn || "",
    manufacturer: c.manufacturer || "",
    description: c.description || "",
    package: c.package || "",
    qty_per_unit: c.qty_per_unit ?? c.quantity_per_unit ?? 1,
    qty_total: c.qty_total ?? c.quantity_total ?? 1,
    unit_price: c.unit_price ?? 0,
    stock: c.stock ?? c.total_stock ?? 0,
    lifecycle: c.lifecycle || "unknown",
    distributor: c.distributor || "",
    distributor_url: c.distributor_url || c.tme_url || "",
    datasheet_url: c.datasheet_url || "",
    mpn_confidence: c.mpn_confidence || "verified",
    verified: c.verified ?? true,
    warnings: c.warnings || [],
    alternatives: c.alternatives || [],
    justification: c.justification || c.constraints_reasoning || "",
  }));

  const notSourced = (data.not_sourced || []).concat(
    rawComponents
      .filter((c: JsonData) => c.status === "not_found" || c.status === "error")
      .map((c: JsonData) => ({ item: c.ref || c.value || "unknown", reason: c.reason || c.status }))
  );

  const summary = data.bom_summary || {
    unique_parts: components.filter((c: JsonData) => c.mpn).length,
    total_components_per_unit: components.reduce((s: number, c: JsonData) => s + (c.qty_per_unit || 0), 0),
    cost_per_unit: components.reduce((s: number, c: JsonData) => s + (c.unit_price || 0) * (c.qty_per_unit || 0), 0),
    cost_total: components.reduce((s: number, c: JsonData) => s + (c.unit_price || 0) * (c.qty_total || 0), 0),
    volume: data.production_volume || 1,
    currency: components[0]?.currency || "USD",
  };

  return (
    <div className="space-y-4">
      {messageTxt && (
        <p className="text-sm text-text-primary leading-relaxed">
          {messageTxt}
        </p>
      )}
      <BOMTable
        components={components}
        notSourced={notSourced.length ? notSourced : undefined}
        summary={summary}
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
// Decision Card (interactive user-choice card)
// ---------------------------------------------------------------------------

interface DecisionOption {
  key: string;
  label: string;
}

interface Decision {
  decision_id: string;
  ref: string;
  mpn: string;
  question: string;
  options: DecisionOption[];
  chosen?: string;
}

function DecisionRequiredView({
  data,
  messageTxt,
  conversationId,
}: {
  data: JsonData;
  messageTxt: string;
  conversationId?: string;
}) {
  const decisions: Decision[] =
    data?.decisions && Array.isArray(data.decisions)
      ? (data.decisions as Decision[])
      : [];
  const taskId = data?.task_id || "";
  const total = decisions.length;

  const [choices, setChoices] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const d of decisions) {
      if (d.chosen) initial[d.decision_id] = d.chosen;
    }
    return initial;
  });
  const [currentIndex, setCurrentIndex] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const resolved = Object.keys(choices).length;
  const allResolved = resolved === total;
  const current = decisions[currentIndex];

  const handleChoice = (decisionId: string, key: string) => {
    setChoices((prev) => ({ ...prev, [decisionId]: key }));
    // Auto-advance after short delay
    setTimeout(() => {
      if (currentIndex < total - 1) {
        setCurrentIndex((i) => i + 1);
      }
    }, 300);
  };

  const handleSubmitAll = async () => {
    if (!conversationId || !allResolved) return;
    setSubmitting(true);
    try {
      // Send all decisions as one batch
      await sendDecision(conversationId, taskId, "__batch__", JSON.stringify(choices));
      setSubmitted(true);
    } finally {
      setSubmitting(false);
    }
  };

  if (!total || !conversationId) return null;

  if (submitted) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-text-primary leading-relaxed">{messageTxt}</p>
        <div className="border border-green-700 rounded-lg p-3 bg-green-950/20">
          <p className="text-sm text-green-400">All {total} decisions submitted. Generating BOM...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {messageTxt && (
        <p className="text-sm text-text-primary leading-relaxed mb-2">
          {messageTxt}
        </p>
      )}

      {/* Progress bar */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all duration-300"
            style={{ width: `${(resolved / total) * 100}%` }}
          />
        </div>
        <span className="text-xs text-text-muted shrink-0">
          {resolved}/{total}
        </span>
      </div>

      {/* Current decision card */}
      {current && (
        <div className="border border-amber-200 dark:border-amber-800 rounded-lg p-4 bg-amber-50 dark:bg-amber-950/20">
          <p className="font-medium text-sm text-text-primary mb-1">
            {current.ref ? `${current.ref}: ` : ""}{current.mpn}
          </p>
          <p className="text-sm text-text-secondary mb-3">{current.question}</p>
          <div className="flex gap-2 flex-wrap">
            {current.options.map((opt) => {
              const isSelected = choices[current.decision_id] === opt.key;
              return (
                <button
                  key={opt.key}
                  onClick={() => handleChoice(current.decision_id, opt.key)}
                  className={`px-3 py-1.5 rounded text-sm transition-colors ${
                    isSelected
                      ? "bg-accent text-white"
                      : "bg-bg-primary border border-border hover:bg-accent/10 hover:border-accent/30"
                  }`}
                >
                  {opt.key}: {opt.label}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Navigation + Submit */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => setCurrentIndex((i) => Math.max(0, i - 1))}
          disabled={currentIndex === 0}
          className="px-3 py-1.5 rounded text-xs text-text-secondary hover:bg-bg-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Prev
        </button>
        <span className="text-xs text-text-muted">
          {currentIndex + 1} / {total}
        </span>
        {allResolved && currentIndex === total - 1 ? (
          <button
            onClick={handleSubmitAll}
            disabled={submitting}
            className="px-4 py-1.5 rounded text-xs bg-accent text-white hover:bg-accent-hover disabled:opacity-50 transition-colors"
          >
            {submitting ? "Submitting..." : "Confirm all"}
          </button>
        ) : (
          <button
            onClick={() => setCurrentIndex((i) => Math.min(total - 1, i + 1))}
            disabled={currentIndex === total - 1}
            className="px-3 py-1.5 rounded text-xs text-text-secondary hover:bg-bg-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MessageBubble
// ---------------------------------------------------------------------------

export default function MessageBubble({ message, conversationId }: MessageBubbleProps) {
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
          ) : parsed.status === "decision_required" && parsed.decisions ? (
            <DecisionRequiredView
              data={parsed}
              messageTxt={parsed.message || ""}
              conversationId={conversationId}
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
