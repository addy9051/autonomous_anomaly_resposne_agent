"""
Seed the Knowledge Base — Ingest sample runbooks into pgvector.

Usage:
    python scripts/seed_knowledge_base.py             # Ingest sample runbooks
    python scripts/seed_knowledge_base.py --check      # Check KB health only
    python scripts/seed_knowledge_base.py --search "latency spike"  # Test search

Targets the Supabase Cloud pgvector instance when SUPABASE_DB_URL is configured,
otherwise falls back to local Docker pgvector.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def seed() -> None:
    """Ingest all sample runbooks into the knowledge base."""
    from knowledge_base.ingestion.pipeline import RunbookIngestionPipeline
    from shared.config import get_settings

    settings = get_settings()
    dsn_target = "Supabase Cloud" if "supabase" in settings.data.rag_dsn else "Local Docker"
    print(f"\nTarget: {dsn_target}")
    print(f"DSN: {settings.data.rag_dsn[:50]}...\n")

    pipeline = RunbookIngestionPipeline()
    total_chunks = await pipeline.ingest_sample_runbooks()

    print("\nIngestion complete!")
    print("Runbooks ingested: 8")
    print(f"Total chunks created: {total_chunks}")
    print("Embedding dimensions: 768 (Matryoshka)")
    print("Embedding model: text-embedding-3-small")


async def check_health() -> None:
    """Check knowledge base health."""
    from knowledge_base.retrieval.search import HybridSearchService

    search = HybridSearchService()
    health = await search.healthcheck()

    if health["status"] == "healthy":
        print("\nKnowledge base is healthy")
        print(f"Documents: {health['document_count']}")
        print(f"Target: {health.get('dsn_target', 'unknown')}")
    else:
        print("\nKnowledge base is unavailable")
        print(f"Error: {health.get('error', 'unknown')}")
        print("\nMake sure to:")
        print("1. Set SUPABASE_DB_URL in .env with your Supabase connection string")
        print("2. Run the migration SQL in your Supabase SQL editor")
        print("3. Or start local Docker: docker compose up -d postgres")


async def test_search(query: str) -> None:
    """Test RAG search with a query."""
    from knowledge_base.retrieval.search import HybridSearchService

    search = HybridSearchService()
    print(f"\nSearching: \"{query}\"\n")

    results = await search.search(query=query)

    if results:
        for i, ref in enumerate(results, 1):
            print(f"{i}. [{ref.similarity_score:.4f}] {ref.title}")
            print(f"   ID: {ref.runbook_id}")
            if ref.relevant_steps:
                print(f"   Steps: {ref.relevant_steps[0]}")
            print()
    else:
        print("No results found. Have you run `--seed` yet?")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Base Management")
    parser.add_argument("--check", action="store_true", help="Check KB health")
    parser.add_argument("--search", type=str, help="Test search with a query")
    args = parser.parse_args()

    if args.check:
        await check_health()
    elif args.search:
        await test_search(args.search)
    else:
        await seed()


if __name__ == "__main__":
    asyncio.run(main())
