import asyncio
import httpx
from api.main import app

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
        res = await client.get("/health")
        print("HEALTH_RESPONSE:", res.status_code, res.json())
        assert res.status_code == 200
        assert res.json()["database"] == "ok"
        print("NEON_DATABASE_HEALTH_STATUS: OK (Neon connection active)")

if __name__ == "__main__":
    asyncio.run(main())
