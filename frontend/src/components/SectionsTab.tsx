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
      .then((res) => { if (!cancelled) { setSections(res.sections); } })
      .catch((e) => { if (!cancelled) setListError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoadingList(false); });
    return () => { cancelled = true; };
  }, [paperId]);

  function scrollTo(order: number) {
    const el = document.getElementById(`section-${order}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function explainSection(section: SectionInfo) {
    const order = section.order;

    // If already explained, just scroll
    if (explains[order]?.text && !explains[order]?.loading) {
      setActive(order);
      scrollTo(order);
      return;
    }

    // Abort any in-progress for this section
    abortsRef.current[order]?.abort();
    const ctrl = new AbortController();
    abortsRef.current[order] = ctrl;

    setActive(order);
    scrollTo(order);

    setExplains((prev) => ({
      ...prev,
      [order]: { text: "", loading: true, progress: "Starting…", error: null },
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

  return (
    <div className="sections-tab-layout">
      {/* High-level heading list — always visible immediately after upload */}
      <div className="headings-list">
        <div className="headings-meta">{sections.length} sections detected</div>
        {sections.map((s) => (
          <button
            key={s.order}
            className={`heading-row ${active === s.order ? "heading-row-active" : ""}`}
            onClick={() => explainSection(s)}
          >
            <span className="heading-num">{s.order + 1}</span>
            <span className="heading-title">{s.title}</span>
            <span className="heading-pages">p.{s.start_page}–{s.end_page}</span>
            {explains[s.order]?.text
              ? <span className="heading-done">✓</span>
              : <span className="heading-explain">Explain →</span>
            }
          </button>
        ))}
      </div>

      {/* Sticky pills for quick jump — visible once you scroll down */}
      <div className="section-pills-sticky">
        {sections.map((s) => (
          <button
            key={s.order}
            className={`section-pill ${active === s.order ? "active" : ""}`}
            onClick={() => explainSection(s)}
          >
            {s.title}
            <span className="pill-pages">p.{s.start_page}</span>
          </button>
        ))}
      </div>

      {/* Section blocks — explanations appear here when clicked */}
      <div className="sections-body">
        {sections.map((s) => {
          const state = explains[s.order];
          return (
            <div
              key={s.order}
              id={`section-${s.order}`}
              className={`section-block ${active === s.order ? "section-block-active" : ""}`}
            >
              <div className="section-block-header">
                <h4 className="section-block-title"
                  onClick={() => explainSection(s)}
                  style={{ cursor: "pointer" }}
                >
                  {s.title}
                </h4>
                <span className="section-block-pages">pp. {s.start_page}–{s.end_page}</span>
                {!state && (
                  <button
                    className="explain-btn"
                    onClick={() => explainSection(s)}
                  >
                    Explain
                  </button>
                )}
                {state?.loading && state.progress && (
                  <span className="progress-badge">
                    <span className="spinner" />
                    {state.progress}
                  </span>
                )}
              </div>

              {state?.error && <div className="error-banner">⚠ {state.error}</div>}

              {state?.text && (
                <article className="markdown section-explanation">
                  <ReactMarkdown>{state.text}</ReactMarkdown>
                  {state.loading && <span className="cursor-blink" />}
                </article>
              )}

              {!state && (
                <div className="section-prompt" onClick={() => explainSection(s)}>
                  Click to get a plain-language explanation of this section.
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
