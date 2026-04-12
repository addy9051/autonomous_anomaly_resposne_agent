import asyncio

from langgraph.checkpoint.redis import AsyncRedisSaver


async def main() -> None:
    try:
        # Create the checkpointer using standard context
        async with AsyncRedisSaver.from_conn_string("redis://localhost:6379"):
            print("Redis is ALIVE and the Checkpointer is Valid! ✅")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
