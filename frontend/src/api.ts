// API client for the Lucid FastAPI backend.
// Calls go through Vite's /api proxy in dev so we don't fight CORS.

const BASE = "/api";

export interface HealthResponse {
  ollama_reachable: boolean;
  available_models: string[];
  papers_loaded: number;
}

export interface IngestResponse {
  paper_id: string;
  filename: string;
  num_pages: number;
  num_sections: number;
  num_chunks_indexed: number;
}

export interface PaperListItem {
  paper_id: string;
  filename: string;
  num_pages: number;
  num_sections: number;
  num_chunks_indexed: number;
  title: string;
}

export interface SectionInfo {
  order: number;
  title: string;
  start_page: number;
  end_page: number;
  text: string;
  level: number;
  section_number: string;
}

export interface SectionsResponse {
  paper_id: string;
  sections: SectionInfo[];
}

export interface SummaryResponse {
  paper_id: string;
  summary: string;
}

export interface ExplainResponse {
  paper_id: string;
  section_title: string;
  explanation: string;
}

export interface SourceInfo {
  page: number | null;
  section: string | null;
  text: string;
  distance: number | null;
}

export interface AskResponse {
  paper_id: string;
  question: string;
  answer: string;
  sources: SourceInfo[];
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  async health(): Promise<HealthResponse> {
    return handle(await fetch(`${BASE}/health`));
  },

  async listPapers(): Promise<PaperListItem[]> {
    return handle(await fetch(`${BASE}/papers`));
  },

  async loadPaper(paperId: string): Promise<IngestResponse> {
    return handle(await fetch(`${BASE}/papers/${paperId}/load`, { method: "POST" }));
  },

  async ingest(file: File): Promise<IngestResponse> {
    const fd = new FormData();
    fd.append("file", file);
    return handle(await fetch(`${BASE}/ingest`, { method: "POST", body: fd }));
  },

  async sections(paperId: string): Promise<SectionsResponse> {
    return handle(await fetch(`${BASE}/sections/${paperId}`));
  },

  async summarize(paperId: string, model?: string): Promise<SummaryResponse> {
    const qs = model ? `?model=${encodeURIComponent(model)}` : "";
    return handle(
      await fetch(`${BASE}/summarize/${paperId}${qs}`, { method: "POST" })
    );
  },

  async explain(
    paperId: string,
    sectionOrder: number,
    model?: string
  ): Promise<ExplainResponse> {
    return handle(
      await fetch(`${BASE}/explain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          paper_id: paperId,
          section_order: sectionOrder,
          model,
        }),
      })
    );
  },

  async ask(
    paperId: string,
    question: string,
    topK = 6,
    model?: string
  ): Promise<AskResponse> {
    return handle(
      await fetch(`${BASE}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          paper_id: paperId,
          question,
          top_k: topK,
          model,
        }),
      })
    );
  },
};
