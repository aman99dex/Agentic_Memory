# 0006. `bge-small-en-v1.5` through fastembed (ONNX)

**Status:** Accepted, 2026-10-16

## Context
The embedder runs on every write and every read. The deploy target is a small CPU
container (ADR 0009). PyTorch plus sentence-transformers is about 1 GB+ of image and
won't fit a 512 MB instance.

## Options
| Model | Size / dims | Notes |
|---|---|---|
| **bge-small-en-v1.5** | 33M / 384 | MIT. Smallest and fastest on CPU. Weaker retrieval than the others. |
| gte-modernbert-base | 149M / 768 | Apache-2.0. Better retrieval, 8k context. |
| EmbeddingGemma-300M | 308M / 768 (Matryoshka) | Strong, but under the Gemma licence terms. |
| Qwen3-Embedding-0.6B | 600M / 1024 | Best scores. Too heavy for a cheap CPU host. |
| API (OpenAI 3-small, Voyage lite) | about $0.02 per 1M tokens | No RAM cost. Adds network latency and a vendor. |

Runtime: **fastembed** runs ONNX models without PyTorch, so the image stays small.

## Decision
bge-small via fastembed. The model weights are baked into the Docker image and loaded
once at startup; `/readyz` fails until loading finishes. Each row stores
`embedding_model`, so a model change becomes a re-embed migration and never a silent mix
of incompatible vectors.

**Phase 5 ablation:** bge-small vs gte-modernbert. If the gain is a few points of recall,
we keep the small model and say so.

**In an interview:** "I chose the embedder for the deploy constraint, a CPU container
with no PyTorch. I measured what the bigger model would buy, and recorded the model
name on each vector so switching is a migration, not corruption."
