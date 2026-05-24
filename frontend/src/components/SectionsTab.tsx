import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, type SectionInfo } from "../api";
import { streamSSE } from "../streamSSE";

interface Props {
  paperId: string;
  model?: string;
}

interface ExplainState {
  text: string;
  loading: boolean;
  progress: string;
  error: string | null;
  fromCache: boolean;
}

export function SectionsTab({ paperId, model }: Props) {
  const [sections, setSections] = useState<SectionInfo[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const [explains, setExplains] = useState<Record<number, ExplainState>>({});
  const abortsRef = useRef<Record<number, AbortController>>({});

  useEffect(() => {
    let cancelled = false;
    setLoadingList(true);
    api.sections(paperId)
      .then((res) => { if (!cancelled) setSections(res.sections); })
      .catch((e) => { if (!cancelled) setListError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoadingList(false); });
    return () => { cancelled = true; };
  }, [paperId]);

  async function explainSection(section: SectionInfo) {
    const order = section.order;

    // Toggle off if already explained and not loading
    if (active === order && explains[order]?.text && !explains[order]?.loading) {
      setActive(null);
      return;
    }

    setActive(order);

    // Already explained — just expand
    if (explains[order]?.text && !explains[order]?.loading) return;

    abortsRef.current[order]?.abort();
    const ctrl = new AbortController();
    abortsRef.current[order] = ctrl;

    setExplains((prev) => ({
      ...prev,
      [order]: { text: "", loading: true, progress: "Starting…", error: null, fromCache: false },
    }));

    try {
      const stream = streamSSE("/api/explain/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paper_id: paperId, section_order: order, model }),
        signal: ctrl.signal,
      });

      for await (const event of stream) {
        if (event.type === "progress") {
          setExplains((prev) => ({
            ...prev,
            [order]: { ...prev[order], progress: event.message ?? "" },
          }));
        } else if (event.type === "token") {
          setExplains((prev) => ({
            ...prev,
            [order]: { ...prev[order], text: prev[order].text + (event.text ?? "") },
          }));
        } else if (event.type === "done") {
          setExplains((prev) => ({
            ...prev,
            [order]: { ...prev[order], fromCache: event.cached === true },
          }));
          break;
        } else if (event.type === "error") {
          throw new Error(event.message ?? "Stream error");
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setExplains((prev) => ({
          ...prev,
          [order]: { ...prev[order], error: e instanceof Error ? e.message : String(e) },
        }));
      }
    } finally {
      setExplains((prev) => ({
        ...prev,
        [order]: { ...prev[order], loading: false, progress: "" },
      }));
    }
  }

  if (loadingList) return <div className="muted">Loading sections…</div>;
  if (listError) return <div className="error-banner">⚠ {listError}</div>;
  if (!sections.length) return <div className="muted">No sections detected.</div>;

  const explained = Object.values(explains).filter((e) => e.text).length;

  return (
    <div className="sections-accordion">
      <div className="sections-meta">
        {sections.length} sections · {explained > 0 && `${explained} explained`}
      </div>

      {sections.map((s) => {
        const state = explains[s.order];
        const isOpen = active === s.order;
        const indent = (s.level ?? 1) - 1;

        return (
          <div key={s.order} className={`accordion-item ${isOpen ? "accordion-open" : ""}`}>
            <button
              className="accordion-header"
              style={{ paddingLeft: `${16 + indent * 20}px` }}
              onClick={() => explainSection(s)}
            >
              <span className="accordion-num">
                {s.section_number || String(s.order + 1)}
              </span>
              <span className="accordion-title">{s.title}</span>
              <span className="accordion-status">
                {state?.loading && (
                  <span className="accordion-progress">
                    <span className="spinner" />
                    {state.progress}
                  </span>
                )}
                {state?.text && !state.loading && state.fromCache && (
                  <span className="cache-badge">⚡ cached</span>
                )}
                {state?.text && !state.loading && (
                  <span className="status-done">✓</span>
                )}
                {!state && <span className="accordion-action">Explain</span>}
              </span>
            </button>

            {isOpen && (
              <div className="accordion-body">
                {state?.error && <div className="error-banner">⚠ {state.error}</div>}

                {!state && (
                  <div className="accordion-empty">
                    Click <strong>Explain</strong> above to generate a plain-language explanation.
                  </div>
                )}

                {state?.text && (
                  <article className="markdown">
                    <ReactMarkdown>{state.text}</ReactMarkdown>
                    {state.loading && <span className="cursor-blink" />}
                  </article>
                )}

                {state?.loading && !state.text && (
                  <div className="accordion-empty muted">{state.progress}</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
