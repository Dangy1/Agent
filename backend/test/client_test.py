import os
import uuid
import asyncio
from typing import Any, Dict

# langgraph-sdk 0.3.x commonly exposes get_client() (Client import may differ across versions)
def get_lg_client():
    try:
        from langgraph_sdk import get_client  # preferred in newer sdk layouts
        return get_client(url=os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024"))
    except Exception:
        # fallback: older layout
        from langgraph_sdk.client import get_client
        return get_client(url=os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024"))


ASSISTANT_ID = os.getenv("LANGGRAPH_ASSISTANT_ID", "agent")  # graph id in langgraph.json
THREAD_ID = os.getenv("LANGGRAPH_THREAD_ID") or str(uuid.uuid4())

CTX: Dict[str, Any] = {
    "user_id": os.getenv("USER_ID", "dang"),
    "session_id": os.getenv("SESSION_ID", "s1"),
}

PROMPT = "Find the most popular wireless headphones right now and check if they're in stock"


def _pretty_event(e: Any) -> str:
    # SDK event shape differs slightly across versions; be defensive.
    try:
        etype = getattr(e, "event", None) or e.get("event")
        data = getattr(e, "data", None) or e.get("data")
        return f"{etype}: {data}"
    except Exception:
        return repr(e)


async def main():
    client = get_lg_client()

    print("\n" + "=" * 90)
    print(f"THREAD={THREAD_ID}  ctx={CTX}")
    print(f"USER: {PROMPT}")
    print("=" * 90)

    # Ensure thread exists (some server configs require explicit creation)
    try:
        await client.threads.create(thread_id=THREAD_ID)
    except Exception:
        # If already exists or server auto-creates, ignore.
        pass

    # Stream run
    stream = client.runs.stream(
        thread_id=THREAD_ID,
        assistant_id=ASSISTANT_ID,
        input={
            "messages": [{"role": "user", "content": PROMPT}],
            # create_agent will expose runtime.context if you pass `context`
            "context": CTX,
        },
        stream_mode=["messages", "updates", "custom"],  # get token chunks + state updates + custom events
    )

    async for event in stream:
        # Print concise
        print(_pretty_event(event))

    print("\nDONE.")


if __name__ == "__main__":
    asyncio.run(main())
