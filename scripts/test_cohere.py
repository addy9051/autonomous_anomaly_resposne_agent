"""
Quick test to verify Cohere cross-encoder reranking is working with your API key.
Run: poetry run python scripts/test_cohere.py
"""

import os
import sys

# Load .env
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("COHERE_API_KEY", "")
if not api_key:
    print("❌ COHERE_API_KEY not found in .env")
    sys.exit(1)

print(f"✅ COHERE_API_KEY loaded ({api_key[:8]}...{api_key[-4:]})")

try:
    import cohere

    print(f"✅ cohere package imported (v{cohere.__version__})")
except ImportError:
    print("❌ cohere package not installed. Run: poetry add cohere")
    sys.exit(1)

# Test reranking with SRE-themed documents
query = "database connection pool exhaustion causing latency spikes"

documents = [
    "When PostgreSQL connection pools are exhausted, new queries queue behind the pool limit. "
    "Check pg_stat_activity for idle-in-transaction connections and increase max_connections or use PgBouncer.",
    "Network packet loss between availability zones can cause intermittent timeout errors. "
    "Check VPC flow logs and MTU settings on the inter-zone links.",
    "Memory pressure on Kubernetes pods triggers OOMKill events. "
    "Review container memory limits and check for memory leaks in the Java heap.",
    "Redis sentinel failover can cause brief connection drops. "
    "Ensure all clients use sentinel-aware connection strings with retry logic.",
    "Connection pool starvation in HikariCP manifests as thread-blocked warnings. "
    "Tune minimumIdle, maximumPoolSize, and connectionTimeout. Monitor via JMX metrics.",
]

print(f'\n🔍 Query: "{query}"')
print(f"📄 Documents: {len(documents)} candidates\n")

try:
    co = cohere.ClientV2(api_key)
    response = co.rerank(
        model="rerank-english-v3.0",
        query=query,
        documents=documents,
        top_n=3,
    )

    print("✅ Cohere reranking successful!\n")
    print("=" * 60)
    print("RERANKED RESULTS (top 3)")
    print("=" * 60)

    for i, result in enumerate(response.results):
        print(f"\n#{i + 1} — Relevance Score: {result.relevance_score:.4f}")
        print(f"   Original Index: {result.index}")
        print(f"   Content: {documents[result.index][:120]}...")

    print("\n" + "=" * 60)
    print("🎉 Cohere cross-encoder reranking is fully operational!")
    print("   Your RAG pipeline will now use rerank-english-v3.0")
    print("   for precision-boosted runbook retrieval.")
    print("=" * 60)

except Exception as e:
    print(f"❌ Cohere API call failed: {e}")
    sys.exit(1)
