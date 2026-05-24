import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { streamSSE } from "../streamSSE";

interface Props {
  paperId: string;
  model?: string;
  autoLoad?: boolean;
}

type Phase = "idle" | "map" | "reduce";

interface LogEntry {
  msg: string;
  done: boolean;
}

export function SummaryTab({ paperId, model, autoLoad = true }: Props) {
  const [summary, setSummary] = useState<string>("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [pct, setPct] = useState<number>(0);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [fromCache, setFromCache] = useState<boolean>(false);
  const abortRef = useRef<AbortController | null>(null);
  const totalRef = useRef<number>(1);

  // Defined at component scope so both useEffect and the button can call it
  async function run(ctrl: AbortController) {
    setSummary("");
    setPhase("map");
    setPct(0);
    setLog([{ msg: "Starting…", done: false }]);
    setError(null);
    setFromCache(false);
    console.log("[Lucid] Summary stream started for paper:", paperId);

    try {
      const qs = model ? `?model=${encodeURIComponent(model)}` : "";
      const stream = streamSSE(`/api/summarize/${paperId}/stream${qs}`, {
        method: "POST",
        signal: ctrl.signal,
      });

      for await (const event of stream) {
        console.log("[Lucid] SSE event:", event);

        if (event.type === "map_start") {
          totalRef.current = event.total ?? 1;
          const msg = `Analysing ${event.total} section${event.total === 1 ? "" : "s"}…`;
          setLog([{ msg, done: false }]);
          setPct(2);

        } else if (event.type === "map_chunk_start") {
          const label = event.label ?? `Part ${event.chunk}`;
          const total = event.total ?? totalRef.current;
          const msg = `Reading & analysing: ${label} (${event.chunk}/${total})`;
          console.log("[Lucid]", msg);
          setLog((prev) => [
            ...prev.slice(-4).map((e) => ({ ...e, done: true })),
            { msg, done: false },
          ]);
          setPct(Math.round(((event.chunk! - 1) / total) * 78) + 2);

        } else if (event.type === "token") {
          setSummary((prev) => prev + (event.text ?? ""));

        } else if (event.type === "reduce_start") {
          const msg = "Writing final structured summary…";
          console.log("[Lucid]", msg);
          setSummary("");
          setPhase("reduce");
          setPct(82);
          setLog((prev) => [
            ...prev.slice(-4).map((e) => ({ ...e, done: true })),
            { msg, done: false },
          ]);

        } else if (event.type === "done") {
          console.log("[Lucid] Summary complete.");
          setPct(100);
          setFromCache(event.cached === true);
          setLog((prev) => prev.map((e) => ({ ...e, done: true })));
          break;

        } else if (event.type === "error") {
          throw new Error(event.message ?? "Stream error");
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        console.error("[Lucid] Summary error:", e);
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setPhase("idle");
    }
  }

  function startGeneration() {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    run(ctrl);
  }

  // SummaryTab is always on-demand — ExplainTab has first claim on Ollama
  // eslint-disable-next-line react-hooks/exhaustive-deps
  void autoLoad; // prop kept for API compat but not used here

  const loading = phase !== "idle";

  return (
    <div className="tab-content">
      {(loading || pct > 0) && (
        <div className="summary-progress">
          <div className="summary-progress-bar-track">
            <div
              className="summary-progress-bar-fill"
              style={{ width: `${pct}%`, transition: pct === 0 ? "none" : "width 0.6s ease" }}
            />
          </div>
          <div className="summary-log">
            {log.map((entry, i) => (
              <div key={i} className={`log-entry ${entry.done ? "log-done" : "log-active"}`}>
                {entry.done
                  ? <span className="log-tick">✓</span>
                  : <span className="spinner log-spinner" />}
                {entry.msg}
              </div>
            ))}
          </div>
        </div>
      )}

      {error && <div className="error-banner">⚠ {error}</div>}

      {!summary && !loading && !error && pct === 0 && (
        <div className="on-demand-prompt">
          <p>Click to generate a quick structured summary.</p>
          <button className="btn" onClick={startGeneration}>Generate Quick Summary</button>
        </div>
      )}

      {summary && !loading && (
        <div style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 8 }}>
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: "6px 12px" }}
            onClick={startGeneration}>
            Regenerate
          </button>
          {fromCache && <span className="cache-badge">⚡ cached</span>}
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
