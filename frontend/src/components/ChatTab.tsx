import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, type SourceInfo } from "../api";

interface Props {
  paperId: string;
  model?: string;
}

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  sources?: SourceInfo[];
  error?: boolean;
}

export function ChatTab({ paperId, model }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, sending]);

  async function send() {
    const q = input.trim();
    if (!q || sending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setSending(true);
    try {
      const res = await api.ask(paperId, q, 6, model);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: res.answer, sources: res.sources },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: e instanceof Error ? e.message : String(e),
          error: true,
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="tab-content chat-tab">
      <div className="chat-messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="empty-state">
            Ask any question about the paper. Answers are grounded in the
            actual text and cite page numbers.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`message message-${m.role}`}>
            <div className={`message-bubble ${m.error ? "error" : ""}`}>
              <ReactMarkdown>{m.text}</ReactMarkdown>
            </div>
            {m.sources && m.sources.length > 0 && (
              <details className="sources">
                <summary>
                  {m.sources.length} source{m.sources.length === 1 ? "" : "s"}
                </summary>
                <ol>
                  {m.sources.map((s, j) => (
                    <li key={j}>
                      <div className="source-meta">
                        {s.page != null && <span>p.{s.page}</span>}
                        {s.section && <span>· {s.section}</span>}
                        {s.distance != null && (
                          <span>· d={s.distance.toFixed(3)}</span>
                        )}
                      </div>
                      <blockquote>{s.text}</blockquote>
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </div>
        ))}
        {sending && (
          <div className="message message-assistant">
            <div className="message-bubble typing">
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
        )}
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          type="text"
          placeholder="Ask a question about the paper…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={sending}
        />
        <button className="btn" type="submit" disabled={sending || !input.trim()}>
          {sending ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
