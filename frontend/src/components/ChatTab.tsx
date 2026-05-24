import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { type SourceInfo } from "../api";
import { streamSSE } from "../streamSSE";

interface Props {
  paperId: string;
  model?: string;
}

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  sources?: SourceInfo[];
  error?: boolean;
  streaming?: boolean;
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
  }, [messages]);

  async function send() {
    const q = input.trim();
    if (!q || sending) return;
    setInput("");
    setSending(true);

    setMessages((m) => [
      ...m,
      { role: "user", text: q },
      { role: "assistant", text: "", streaming: true },
    ]);

    try {
      const stream = streamSSE("/api/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paper_id: paperId, question: q, top_k: 6, model }),
      });

      let accumulated = "";

      for await (const event of stream) {
        if (event.type === "token" && event.text) {
          accumulated += event.text;
          setMessages((m) => {
            const copy = [...m];
            copy[copy.length - 1] = { ...copy[copy.length - 1], text: accumulated };
            return copy;
          });
        } else if (event.type === "done") {
          setMessages((m) => {
            const copy = [...m];
            copy[copy.length - 1] = {
              role: "assistant",
              text: accumulated,
              sources: event.sources as SourceInfo[] | undefined,
            };
            return copy;
          });
        } else if (event.type === "error") {
          setMessages((m) => {
            const copy = [...m];
            copy[copy.length - 1] = {
              role: "assistant",
              text: event.message ?? "An error occurred",
              error: true,
            };
            return copy;
          });
        }
      }
    } catch (e) {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = {
          role: "assistant",
          text: e instanceof Error ? e.message : String(e),
          error: true,
        };
        return copy;
      });
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
              {m.streaming && m.text === "" ? (
                <div className="typing">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              ) : (
                <>
                  <ReactMarkdown>{m.text}</ReactMarkdown>
                  {m.streaming && <span className="cursor-blink" />}
                </>
              )}
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
