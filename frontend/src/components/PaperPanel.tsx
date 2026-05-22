import type { IngestResponse } from "../api";
import { ExplainTab } from "./ExplainTab";
import { SectionsTab } from "./SectionsTab";
import { SummaryTab } from "./SummaryTab";
import { ChatTab } from "./ChatTab";

interface Props {
  paper: IngestResponse;
  model?: string;
  onUploadAnother: () => void;
}

export function PaperPanel({ paper, model, onUploadAnother }: Props) {
  return (
    <div className="workspace">
      <div className="workspace-left">
        <section className="paper-card">
          <div>
            <h2>{paper.filename}</h2>
            <div className="paper-meta">
              {paper.num_pages} pages · {paper.num_sections} sections ·{" "}
              {paper.num_chunks_indexed} chunks indexed
            </div>
          </div>
          <button className="btn btn-secondary" onClick={onUploadAnother}>
            Upload another
          </button>
        </section>

        <div className="left-section">
          <div className="section-heading">
            <span>Section Derivation</span>
            <span className="section-heading-sub">detected structure · click to explain</span>
          </div>
          <SectionsTab paperId={paper.paper_id} model={model} />
        </div>

        <div className="left-section">
          <div className="section-heading">
            <span>Explanation</span>
            <span className="section-heading-sub">8-section deep breakdown</span>
          </div>
          <ExplainTab paperId={paper.paper_id} model={model} />
        </div>
      </div>

      <div className="workspace-right">
        <div className="chat-panel-heading">Chat with the paper</div>
        <ChatTab paperId={paper.paper_id} model={model} />
      </div>
    </div>
  );
}
