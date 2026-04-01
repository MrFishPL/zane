"use client";

interface StatusIndicatorProps {
  status: string | null;
  agentStatus: "running" | "completed" | "failed" | "idle";
}

/** Detect decision-waiting status text */
function isDecisionWaiting(text: string): boolean {
  const lower = text.toLowerCase();
  return lower.includes("waiting for your decision") || lower.includes("decision");
}

export default function StatusIndicator({
  status,
  agentStatus,
}: StatusIndicatorProps) {
  // Show status for running agents, and also for idle agents with decision-waiting text
  const isWaitingForDecision =
    agentStatus === "idle" && !!status && isDecisionWaiting(status);

  if (agentStatus !== "running" && !isWaitingForDecision) return null;

  const displayText = status || "Processing...";

  // Use amber/warning styling for decision-waiting, accent for normal processing
  const dotColor = isWaitingForDecision ? "bg-amber-500" : "bg-accent";
  const textColor = isWaitingForDecision
    ? "text-amber-600 dark:text-amber-400"
    : "text-text-secondary";

  return (
    <div className="flex items-center gap-2 px-4 py-2 animate-fade-in">
      <div className="flex items-center gap-0.5">
        <span className={`status-dot-1 inline-block w-1.5 h-1.5 rounded-full ${dotColor}`} />
        <span className={`status-dot-2 inline-block w-1.5 h-1.5 rounded-full ${dotColor}`} />
        <span className={`status-dot-3 inline-block w-1.5 h-1.5 rounded-full ${dotColor}`} />
      </div>
      <span className={`text-sm ${textColor} status-pulse`}>
        {displayText}
      </span>
    </div>
  );
}
