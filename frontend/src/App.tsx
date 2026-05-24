import { useEffect, useState } from "react";
import { api, type HealthResponse, type IngestResponse, type PaperListItem } from "./api";
import { Uploader } from "./components/Uploader";
import { HealthBadge } from "./components/HealthBadge";
import { PaperPanel } from "./components/PaperPanel";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [paper, setPaper] = useState<IngestResponse | null>(null);
  const [model, setModel] = useState<string>("");
  const [recentPapers, setRecentPapers] = useState<PaperListItem[]>([]);
  const [openingId, setOpeningId] = useState<string | null>(null);

  async function refreshHealth() {
    try {
      const h = await api.health();
      setHealth(h);
      setHealthError(null);
      if (!model && h.available_models.length > 0) {
        const pref =
          h.available_models.find((m) => m.startsWith("qwen2.5:14b")) ??
          h.available_models[0];
        setModel(pref);
      }
    } catch (e) {
      setHealth(null);
      setHealthError(e instanceof Error ? e.message : String(e));
    }
  }

  async function refreshRecentPapers() {
    try {
      const list = await api.listPapers();
      setRecentPapers(list);
    } catch {
      // silently ignore — backend may not be up yet
    }
  }

  async function openRecentPaper(item: PaperListItem) {
    setOpeningId(item.paper_id);
    try {
      const loaded = await api.loadPaper(item.paper_id);
      setPaper(loaded);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setOpeningId(null);
    }
  }

  function handleUploaded(p: IngestResponse) {
    setPaper(p);
    refreshRecentPapers();
  }

  useEffect(() => {
    refreshHealth();
    refreshRecentPapers();
    const id = window.setInterval(refreshHealth, 15000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={`app ${paper ? "app-wide" : ""}`}>
      <header className="app-header">
        <div className="brand">
          <img src="/lucid.svg" alt="" className="brand-logo" />
          <div>
            <h1>Lucid</h1>
            <p className="tagline">Understand long, dense white papers.</p>
          </div>
        </div>
        <div className="header-right">
          {health && health.available_models.length > 0 && (
            <div className="model-row">
              <label htmlFor="model-select">LLM:</label>
              <select
                id="model-select"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                {health.available_models.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
          )}
          <HealthBadge health={health} error={healthError} onRefresh={refreshHealth} />
        </div>
      </header>

      {!paper && (
        <>
          <Uploader onUploaded={handleUploaded} />

          {recentPapers.length > 0 && (
            <div className="recent-papers">
              <div className="recent-papers-heading">Previously ingested papers</div>
              {recentPapers.map((item) => (
                <div key={item.paper_id} className="recent-paper-row">
                  <div className="recent-paper-info">
                    <div className="recent-paper-title">{item.title}</div>
                    <div className="recent-paper-meta">
                      {item.num_sections} sections · {item.num_pages} pages · {item.num_chunks_indexed} chunks
                    </div>
                  </div>
                  <button
                    className="btn btn-secondary recent-paper-open"
                    onClick={() => openRecentPaper(item)}
                    disabled={openingId === item.paper_id}
                  >
                    {openingId === item.paper_id ? "Loading…" : "Open"}
                  </button>
                </div>
              ))}
            </div>
          )}

          <footer className="app-footer">
            Local-first · Ollama + LanceDB · no data leaves your machine
          </footer>
        </>
      )}

      {paper && (
        <PaperPanel
          key={paper.paper_id}
          paper={paper}
          model={model || undefined}
          onUploadAnother={() => setPaper(null)}
        />
      )}
    </div>
  );
}
