# Local Embeddings (FastEmbed)

`EMBEDDING_PROVIDER=fastembed` runs the embedding model on CPU inside the application process, with no embedding endpoint and no network at runtime. The default provider, `openai_compatible`, is unchanged.

Code: [`shared/local_embeddings.py`](../shared/local_embeddings.py), selected by `get_embeddings()` in [`shared/llm.py`](../shared/llm.py). Tests: [`tests/unit/test_local_embeddings.py`](../tests/unit/test_local_embeddings.py).

## Origin

The idea comes from the hosted SaaS variant in [`saas/`](../saas) (`saas/core/llm.py`, `LocalFastEmbeddings`), which uses free local FastEmbed vectors with pgvector. This is a port adapted to the enterprise root, not a copy. What was taken and what was not:

| From the SaaS | In the root |
|---|---|
| Local FastEmbed provider on CPU | Ported, with the changes below |
| bge-small, 384 dimensions | **Not used.** The root column is `vector(768)`. The default is `BAAI/bge-base-en-v1.5` (768), the same model the Modal/vLLM embedding server serves, so no schema change and no re-embedding |
| HNSW / IVFFlat index setup | Not needed. Migration `001_initial.sql` already creates an HNSW index |
| pgvectorscale and pg_textsearch detection | Not needed. The root uses ParadeDB `pg_search` for BM25 |
| Tenant-isolated search (`tenant_id`, row-level security) | **Not ported.** The enterprise root is single-tenant by design |

## Changes from the SaaS provider

- **Fails closed.** FastEmbed downloads missing weights from the internet, and that download does not pass through `shared/egress.py`. The root provider always loads with `local_files_only=True`, so a missing model raises `LocalEmbeddingsUnavailableError` instantly, with no download and nothing written to disk. Verified against the installed FastEmbed 0.9.0.
- **Size guard.** A model whose output size differs from `EMBEDDING_DIMENSIONS` is refused (from FastEmbed's static catalogue before any load, and by checking the real output on first use for models not in the catalogue). Mixing sizes in one pgvector column would silently corrupt retrieval.
- **Off the event loop.** The SaaS version ran CPU inference inside `async` methods, blocking the loop. Here `aembed_*` use `asyncio.to_thread`.
- **Loaded once per process.** Retrieval services are built per request, and loading ONNX weights is slow.
- **Same vectors as the server.** Queries and documents are embedded identically, with no query-instruction prefix, matching what the OpenAI-compatible server does for the same model.

## Use

```powershell
# 1. On a machine with internet access (this refuses to run while EGRESS_ENFORCE=true):
$env:EGRESS_ENFORCE = "false"
python -m shared.local_embeddings provision          # about 208 MB into models/fastembed

# 2. Copy models/fastembed into the sandbox (it is gitignored), then set:
#    EMBEDDING_PROVIDER=fastembed
#    EMBEDDING_LOCAL_MODEL=BAAI/bge-base-en-v1.5
#    EMBEDDING_LOCAL_CACHE_DIR=models/fastembed
#    EMBEDDING_DIMENSIONS=768
```

If you switch an existing deployment from the server to the local provider with the same model, existing vectors stay valid. With a different model, re-embed everything first.

## Measured (one machine, one run)

| Check | Result |
|---|---|
| Provision (download) | about 27 s, 208 MB |
| Offline load, first call | about 0.8 s |
| Vector size | 768, unit norm |
| Similarity, "database connection pool exhausted" vs "the db has no free connections" | 0.77 |
| Similarity, same first sentence vs "kubernetes pod was OOMKilled" | 0.56 |
| Warm query | about 20 ms |

These are a smoke test on a developer machine, not a retrieval-quality evaluation. Whether local vectors retrieve as well as the server's has not been measured; use `evals/retrieval_ab.py` to compare before relying on it.

## Security notes

- **Model supply chain.** Weights come from FastEmbed's published sources at provisioning time. Record the model name and a hash of the cache directory when provisioning, and treat a swapped weights file as a poisoned retrieval source.
- **Provisioning bypasses the egress guard on purpose.** It is an operator action for a connected machine and refuses to run while egress enforcement is on. Do not run it inside the sandbox.
- **No new runtime network path.** With `fastembed`, embedding makes no HTTP call, which removes the embedding endpoint from the egress allowlist for retrieval.
