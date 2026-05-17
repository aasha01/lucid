import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { streamSSE } from "../streamSSE";

interface Props {
  paperId: string;
  model?: string;
}

type Phase = "idle" | "map" | "reduce";

export function SummaryTab({ paperId, model }: Props) {
  const [summary, setSummary] = useState<string>("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    run(ctrl);
    return () => ctrl.abort();

    async function run(ctrl: AbortController) {
      setSummary("");
      setProgress("Starting…");
      setError(null);
      setPhase("map");

      try {
        const qs = model ? `?model=${encodeURIComponent(model)}` : "";
        const stream = streamSSE(`/api/summarize/${paperId}/stream${qs}`, {
          method: "POST",
          signal: ctrl.signal,
        });

        for await (const event of stream) {
          if (event.type === "map_start") {
            setProgress(`Analyzing ${event.total} part${event.total === 1 ? "" : "s"}…`);
          } else if (event.type === "map_chunk_start") {
            setProgress(`Summarizing part ${event.chunk} of ${event.total}…`);
          } else if (event.type === "token") {
            setSummary((prev) => prev + (event.text ?? ""));
          } else if (event.type === "reduce_start") {
            setSummary("");
            setPhase("reduce");
            setProgress("Writing final summary…");
          } else if (event.type === "done") {
            setProgress("");
            break;
          } else if (event.type === "error") {
            throw new Error(event.message ?? "Stream error");
          }
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        setPhase("idle");
        setProgress("");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId]);

  const loading = phase !== "idle";

  return (
    <div className="tab-content">
      {loading && (
        <div className="progress-bar-row">
          <span className="spinner" />
          <span className="progress-bar-text">{progress || "Working…"}</span>
        </div>
      )}

      {error && (
        <div className="error-banner">
          ⚠ {error}
        </div>
      )}

      {summary && (
        <article className={`markdown ${phase === "map" ? "map-preview" : ""}`}>
          {phase === "map" && <div className="phase-label">Reading chunks…</div>}
          <ReactMarkdown>{summary}</ReactMarkdown>
          {loading && <span className="cursor-blink" />}
        </article>
      )}
    </div>
  );
}
