import type { HealthResponse } from "../api";

interface Props {
  health: HealthResponse | null;
  error: string | null;
  onRefresh: () => void;
}

export function HealthBadge({ health, error, onRefresh }: Props) {
  const status =
    error || !health
      ? "down"
      : health.ollama_reachable
        ? "ok"
        : "degraded";
  const label =
    status === "ok"
      ? "Backend & Ollama online"
      : status === "degraded"
        ? "Backend up · Ollama unreachable"
        : "Backend unreachable";
  return (
    <button
      className={`health-badge health-${status}`}
      onClick={onRefresh}
      title={error ?? "Click to refresh"}
    >
      <span className="dot" />
      <span>{label}</span>
    </button>
  );
}
