import { useRef, useState } from "react";
import { api, type IngestResponse } from "../api";

interface Props {
  onUploaded: (paper: IngestResponse) => void;
}

export function Uploader({ onUploaded }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progressText, setProgressText] = useState<string>("");

  async function handleFile(file: File) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files are supported.");
      return;
    }
    setError(null);
    setUploading(true);
    setProgressText(`Uploading ${file.name}...`);
    try {
      const res = await api.ingest(file);
      onUploaded(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
      setProgressText("");
    }
  }

  return (
    <section
      className={`uploader ${dragOver ? "uploader-drag" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files?.[0];
        if (file) handleFile(file);
      }}
      onClick={() => !uploading && inputRef.current?.click()}
      role="button"
      tabIndex={0}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
          e.target.value = "";
        }}
      />
      <div className="uploader-inner">
        <div className="upload-icon" aria-hidden>
          ↑
        </div>
        <h2>{uploading ? progressText : "Drop a PDF here"}</h2>
        <p>
          {uploading
            ? "Parsing and embedding chunks — this can take 30–60s for a long paper."
            : "or click to browse. arXiv, IEEE, conference proceedings all work."}
        </p>
        {error && <div className="error-banner">⚠ {error}</div>}
      </div>
    </section>
  );
}
