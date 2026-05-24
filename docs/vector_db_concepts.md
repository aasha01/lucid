# Vector DB, Semantic Search & Related Concepts

---

## Start here: what is a number that means something?

Imagine you had to describe a fruit using only numbers:

```
Apple:  [sweetness=7, crunchiness=9, size=4, color_red=8]
Banana: [sweetness=8, crunchiness=2, size=6, color_red=1]
Cherry: [sweetness=9, crunchiness=5, size=1, color_red=9]
```

Now Apple = `[7, 9, 4, 8]` and Cherry = `[9, 5, 1, 9]`. These are **vectors** — just lists of numbers that describe something.

Notice: Apple and Cherry have similar numbers (both sweet, both reddish). Banana is very different. This similarity is **meaningful**.

---

## What an Embedding is

An **embedding** is this same idea but for words, sentences, or paragraphs — a machine learning model converts text into a list of numbers where **similar meaning → similar numbers**.

```
"The model uses Adam optimizer"     → [0.23, -0.81, 0.44, 0.91, ...]
"We trained with Adam and SGD"      → [0.21, -0.79, 0.41, 0.88, ...]  ← similar!
"The cat sat on the mat"            → [0.87,  0.12, -0.63, 0.02, ...]  ← very different
```

Real embeddings have **768 or 1536 numbers** per sentence (not 4 like the fruit example), but the idea is identical.

The key insight: **you never design these numbers yourself** — a pre-trained model (like `nomic-embed-text` which Lucid uses via Ollama) learns them from reading billions of sentences.

---

## What Semantic Search is

**Keyword search** (old way): find the exact word in the text.
```
Query: "optimizer"
Finds: only documents containing the word "optimizer"
Misses: "we trained using Adam with β1=0.9" (no word "optimizer" appears)
```

**Semantic search** (vector way): find text with similar *meaning*.
```
Query: "what training algorithm did they use?"
Embedding: [0.11, -0.74, 0.39, ...]

Compare against all stored chunks:
  "Adam optimizer with β1=0.9..."      distance: 0.05  ← very close
  "We used SGD with momentum..."       distance: 0.09  ← also close
  "The encoder has 6 layers..."        distance: 0.87  ← far away, different topic

Return the closest ones.
```

It finds *"Adam optimizer"* even though your question said *"training algorithm"* — because those phrases mean the same thing to the model.

---

## What a Vector DB is

A regular database stores rows of data and searches by exact match:
```sql
SELECT * FROM chunks WHERE section = 'Methods'
```

A **Vector DB** stores vectors alongside data and searches by **distance**:
```
Give me the 5 chunks whose vectors are closest to this query vector
```

It is a database optimised for one specific operation: **nearest neighbour search** in high-dimensional space.

### A concrete example with Lucid

When you ingest "Attention Is All You Need":

```
LanceDB stores:
┌─────────────────────────────────┬──────────────────────────────────┬──────────┐
│ text                            │ vector (768 numbers)             │ section  │
├─────────────────────────────────┼──────────────────────────────────┼──────────┤
│ "We used Adam optimizer..."     │ [0.23, -0.81, 0.44, ...]        │ Training │
│ "The encoder consists of..."    │ [0.87,  0.12, -0.63, ...]       │ Model    │
│ "BLEU score of 28.4..."         │ [-0.34, 0.55, 0.71, ...]        │ Results  │
│ ... (200 more rows)             │ ...                             │ ...      │
└─────────────────────────────────┴──────────────────────────────────┴──────────┘
```

When you ask *"What optimizer was used?"*:
1. Embed the question → `[0.21, -0.79, 0.41, ...]`
2. LanceDB finds the 6 rows with the closest vectors
3. Those 6 chunks get sent to the LLM as context
4. LLM answers: *"The authors used Adam optimizer with β1=0.9, β2=0.98"*

---

## What "distance" between vectors means

Think of vectors as **points in space**. Two similar sentences are points that sit close together. Dissimilar sentences are far apart.

```
         Methods/Training cluster
              *  *
            *      *   ← "Adam optimizer", "SGD", "learning rate"
              *  *

                              Results cluster
                                   *  *
                                 *      *  ← "BLEU score", "accuracy", "F1"
                                   *  *
```

"Find nearest neighbours" = draw a circle around your query point, return whatever is inside.

The measurement used is usually **cosine similarity** — it measures the angle between two vectors, not the raw distance. Angle near 0° = almost identical meaning. Angle near 90° = completely unrelated.

---

## What Adam Optimizer is

This is unrelated to Vector DBs — it is a concept from machine learning.

Training a neural network means adjusting millions of numbers (weights) to reduce errors. **Gradient descent** is the algorithm: measure the error, nudge each weight in the direction that reduces it.

**Adam** (Adaptive Moment Estimation) is a smarter version of that nudge:
- It gives **bigger steps** to weights that have not moved much
- It gives **smaller steps** to weights that are already changing a lot
- Essentially it auto-tunes the learning speed per weight

```
Plain gradient descent:  all weights get the same sized nudge
Adam:                    each weight gets a nudge sized to its own history
```

It is the standard choice for training transformers (like the Attention Is All You Need paper) because it converges faster and is more stable.

---

## Why Lucid uses a Vector DB

A research paper is 10,000–30,000 words. An LLM context window holds ~4,000–8,000 words. You cannot fit the whole paper into one prompt.

So when the user asks *"What optimizer did they use?"*, without a vector DB you have two bad options:

1. Send the entire paper → does not fit, or costs too much
2. Guess which page to send → likely wrong

The vector DB solves this by doing semantic search to find exactly the right 800 words out of 20,000.

### The two separate features in Lucid

| Feature | Uses Vector DB? | Why |
|---|---|---|
| Summarize paper | No | Reads sections directly, builds an excerpt |
| Explain section | No | Section text is already known, passed straight to LLM |
| Ask a question (`/ask`) | **Yes** | Needs to find relevant chunks across the whole paper |

---

## The full Lucid pipeline in one diagram

```
                        INGEST (once)
PDF ──GROBID──► Sections ──chunk──► 200 chunks
                                        │
                                   embed each chunk
                                   (768 numbers each)
                                        │
                                   store in LanceDB ◄─── persists to disk

                        QUERY (every question)
"What optimizer?"
     │
  embed question
     │
  LanceDB: find 6 nearest chunks  ──► ["Adam optimizer...", "β1=0.9...", ...]
     │
  LLM: read those 6 chunks + question
     │
  Answer: "They used Adam with β1=0.9, β2=0.98"
```

The expensive part (embedding 200 chunks) happens **once at ingest**. Every question after that is fast because it is just a search + one LLM call.

---

## Popular Vector DBs (for reference)

| Name | Notes |
|---|---|
| **LanceDB** | What Lucid uses. Embedded, no server needed, pure Python |
| **ChromaDB** | Popular for local prototypes, similar to LanceDB |
| **Pinecone** | Cloud-hosted, production scale |
| **Weaviate** | Open source, self-hosted, feature-rich |
| **pgvector** | Postgres extension — vector search inside a regular SQL DB |

LanceDB was chosen for Lucid because it runs entirely on your machine with no external service required (just like Ollama).
