"""Quick sanity check for RAG pipeline imports and configuration."""
import sys

sys.path.insert(0, ".")

from knowledge_base.ingestion.pipeline import RunbookIngestionPipeline
from knowledge_base.retrieval.search import HybridSearchService
from shared.config import get_settings

s = get_settings()
target = "supabase" if "supabase" in s.data.rag_dsn else "local"
print(f"rag_dsn target: {target}")
print(f"rag_dsn: {s.data.rag_dsn[:60]}...")

p = RunbookIngestionPipeline()
print(f"Embedding dims: {p.embeddings.dimensions}")
print(f"Runbooks available: {len(p._get_sample_runbooks())}")

h = HybridSearchService()
print(f"Search embedding dims: {h.embeddings.dimensions}")
print(f"Top-k: {h.top_k}")

print("\n✅ All RAG imports and configuration OK")
