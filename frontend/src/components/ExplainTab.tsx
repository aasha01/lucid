import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { streamSSE } from "../streamSSE";

interface Props {
  paperId: string;
  model?: string;
}

type MilestoneStatus = "pending" | "active" | "done";

interface Milestone {
  step: string;
  label: string;
  status: MilestoneStatus;
  detail?: string[];
}

const STEPS: { step: string; label: string }[] = [
  { step: "sections", label: "Detecting sections" },
  { step: "prompt",   label: "Building prompt"    },
  { step: "generate", label: "Generating"          },
];

type Phase = "idle" | "running" | "done";

export function ExplainTab({ paperId, model }: Props) {
  const [explanation, setExplanation] = useState<string>("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [milestones, setMilestones] = useState<Milestone[]>(
    STEPS.map((s) => ({ ...s, status: "pending" }))
  );
  const [wordCount, setWordCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  function setMilestoneState(step: string, status: MilestoneStatus, detail?: string[]) {
    setMilestones((prev) =>
      prev.map((m) =>
        m.step === step ? { ...m, status, ...(detail ? { detail } : {}) } : m
      )
    );
  }

  async function run(ctrl: AbortController) {
    setExplanation("");
    setWordCount(0);
    setPhase("running");
    setError(null);
    setMilestones(STEPS.map((s) => ({ ...s, status: "pending" })));
    console.log("[Lucid] Explain-paper stream started for:", paperId);

    try {
      const qs = model ? `?model=${encodeURIComponent(model)}` : "";
      const stream = streamSSE(`/api/explain-paper/${paperId}/stream${qs}`, {
        method: "POST",
        signal: ctrl.signal,
      });

      for await (const event of stream) {
        if (event.type === "milestone") {
          const { step, status, message, detail } = event;
          if (!step || !status) continue;
          if (status === "start") {
            setMilestoneState(step, "active", detail);
            console.log(`[Lucid] ▶ ${message}`);
          } else if (status === "done") {
            setMilestoneState(step, "done", detail);
            console.log(`[Lucid] ✓ ${message}`);
          }
        } else if (event.type === "token") {
          setExplanation((prev) => {
            const next = prev + (event.text ?? "");
            setWordCount(next.split(/\s+/).filter(Boolean).length);
            return next;
          });
        } else if (event.type === "done") {
          console.log("[Lucid] Explanation complete.");
          setPhase("done");
          break;
        } else if (event.type === "error") {
          throw new Error(event.message ?? "Stream error");
        }
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        // Aborted by StrictMode cleanup or by start() — do NOT reset phase.
        // Another run() call may already be in progress.
        return;
      }
      console.error("[Lucid] Explain error:", e);
      setError(e instanceof Error ? e.message : String(e));
      setPhase("idle");
    }
  }

  function start() {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    run(ctrl);
  }

  // No auto-start — user clicks "Generate Explanation" to begin.

  const loading = phase === "running";

  return (
    <div className="tab-content">

      {/* Milestone list — always visible during and after generation */}
      {(loading || phase === "done") && (
        <div className="milestone-list">
          {milestones.map((m) => (
            <div key={m.step} className={`milestone milestone-${m.status}`}>
              <span className="milestone-icon">
                {m.status === "done"    ? "✓"
                : m.status === "active" ? <span className="spinner milestone-spinner" />
                :                         "·"}
              </span>
              <div className="milestone-body">
                <span className="milestone-label">{m.label}</span>
                {m.detail && m.status === "done" && (
                  <div className="milestone-detail">
                    {m.detail.map((d, i) => (
                      <span key={i} className="milestone-chip">{d}</span>
                    ))}
                  </div>
                )}
              </div>
              {m.step === "generate" && m.status === "active" && wordCount > 0 && (
                <span className="milestone-count">{wordCount} words</span>
              )}
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="error-banner">
          ⚠ {error}
          <button className="btn btn-secondary"
            style={{ marginLeft: 12, fontSize: 12, padding: "4px 10px" }}
            onClick={start}>Retry</button>
        </div>
      )}

      {phase === "idle" && !explanation && !error && (
        <div className="on-demand-prompt">
          <p>Generate a deep 8-section breakdown of this paper.</p>
          <button className="btn" onClick={start}>Generate Explanation</button>
        </div>
      )}

      {phase === "done" && (
        <button className="btn btn-secondary"
          style={{ fontSize: 12, padding: "6px 12px", marginBottom: 12 }}
          onClick={start}>Regenerate</button>
      )}

      {explanation && (
        <article className="markdown explain-article">
          <ReactMarkdown>{explanation}</ReactMarkdown>
          {loading && <span className="cursor-blink" />}
        </article>
      )}
    </div>
  );
}
