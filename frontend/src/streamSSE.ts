// Reads a text/event-stream response and yields parsed JSON events.
// Works with POST requests (unlike EventSource which only supports GET).

export interface SSEEvent {
  type: "progress" | "token" | "done" | "error" | "map_start" | "map_chunk_start" | "reduce_start";
  message?: string; // progress
  text?: string;    // token
  total?: number;   // map_start, map_chunk_start
  chunk?: number;   // map_chunk_start
}

export async function* streamSSE(
  url: string,
  init: RequestInit
): AsyncGenerator<SSEEvent> {
  const res = await fetch(url, init);
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

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE lines are separated by \n\n
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      for (const line of part.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            yield JSON.parse(line.slice(6)) as SSEEvent;
          } catch {
            /* malformed line — skip */
          }
        }
      }
    }
  }
}
