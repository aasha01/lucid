"""
Quick backend smoke test for /explain-paper/stream.
Run from the project root after uploading a paper in the UI:

    .venv\Scripts\python test_explain.py [paper_id]

If no paper_id is given, fetches /health to show loaded papers.
Prints every SSE event so you can confirm the milestone + token stream works.
"""
import json
import sys
import time

import requests

BASE = "http://localhost:8000"


def check_health() -> dict:
    r = requests.get(f"{BASE}/health", timeout=10)
    r.raise_for_status()
    return r.json()


def stream_explain(paper_id: str, model: str | None = None) -> None:
    qs = f"?model={model}" if model else ""
    url = f"{BASE}/explain-paper/{paper_id}/stream{qs}"
    print(f"\n→ POST {url}\n{'─'*60}")

    start = time.time()
    token_count = 0

    with requests.post(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        for raw in r.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8")
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")
            if etype == "milestone":
                icon = "✓" if event.get("status") == "done" else "▶"
                detail = event.get("detail", [])
                detail_str = f"  [{', '.join(detail)}]" if detail else ""
                print(f"  {icon} MILESTONE [{event.get('step')}] {event.get('message')}{detail_str}")
            elif etype == "token":
                token_count += 1
                if token_count == 1:
                    print(f"  ▶ TOKEN stream started…")
                elif token_count % 50 == 0:
                    elapsed = time.time() - start
                    print(f"    {token_count} tokens  ({elapsed:.1f}s)")
                # print(event.get("text", ""), end="", flush=True)  # uncomment for raw text
            elif etype == "done":
                elapsed = time.time() - start
                print(f"\n{'─'*60}")
                print(f"✓ DONE — {token_count} tokens in {elapsed:.1f}s")
            elif etype == "error":
                print(f"\n✗ ERROR — {event.get('message')}")
            elif etype == "progress":
                print(f"  · PROGRESS: {event.get('message')}")


def main() -> None:
    print("── Lucid backend smoke test ──")

    health = check_health()
    print(f"Ollama reachable: {health['ollama_reachable']}")
    print(f"Models: {health['available_models']}")
    print(f"Papers loaded: {health['papers_loaded']}")

    if len(sys.argv) < 2:
        print("\nNo paper_id given. Upload a paper in the UI first, then run:")
        print("  .venv\\Scripts\\python test_explain.py <paper_id>")
        print("\nThe paper_id is shown in the UI under the filename after upload.")
        sys.exit(0)

    paper_id = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else None
    stream_explain(paper_id, model)


if __name__ == "__main__":
    main()
