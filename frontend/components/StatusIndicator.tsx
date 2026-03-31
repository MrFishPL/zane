"use client";

interface StatusIndicatorProps {
  status: string | null;
  agentStatus: "running" | "completed" | "failed" | "idle";
}

export default function StatusIndicator({
  status,
  agentStatus,
}: StatusIndicatorProps) {
  if (agentStatus !== "running") return null;

  const displayText = status || "Processing...";

  return (
    <div className="flex items-center gap-2 px-4 py-2 animate-fade-in">
      <div className="flex items-center gap-0.5">
        <span className="status-dot-1 inline-block w-1.5 h-1.5 rounded-full bg-accent" />
        <span className="status-dot-2 inline-block w-1.5 h-1.5 rounded-full bg-accent" />
        <span className="status-dot-3 inline-block w-1.5 h-1.5 rounded-full bg-accent" />
      </div>
      <span className="text-sm text-text-secondary status-pulse">
        {displayText}
      </span>
    </div>
  );
}
