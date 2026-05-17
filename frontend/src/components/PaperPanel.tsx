import type { IngestResponse } from "../api";
import { SummaryTab } from "./SummaryTab";
import { SectionsTab } from "./SectionsTab";
import { ChatTab } from "./ChatTab";

interface Props {
  paper: IngestResponse;
  model?: string;
  onUploadAnother: () => void;
}

export function PaperPanel({ paper, model, onUploadAnother }: Props) {
  return (
    <div className="workspace">
      {/* Left: scrollable summary + sections */}
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
          <div className="section-heading">Summary</div>
          <SummaryTab paperId={paper.paper_id} model={model} />
        </div>

        <div className="left-section">
          <div className="section-heading">Sections</div>
          <SectionsTab paperId={paper.paper_id} model={model} />
        </div>
      </div>

      {/* Right: sticky chat panel */}
      <div className="workspace-right">
        <div className="chat-panel-heading">Chat with the paper</div>
        <ChatTab paperId={paper.paper_id} model={model} />
      </div>
    </div>
  );
}
