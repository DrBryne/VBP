import asyncio
from google.adk.sessions import InMemorySessionService

async def test():
    svc = InMemorySessionService()
    session = await svc.create_session("app", "user", "s1")
    session.state["test_key"] = "test_value"
    session2 = await svc.get_session("app", "user", "s1")
    print(session2.state)

asyncio.run(test())
