import pytest
from google.adk.sessions import InMemorySessionService

@pytest.mark.asyncio
async def test_state_behavior():
    svc = InMemorySessionService()
    session = await svc.create_session(app_name="app", user_id="user", session_id="s1")
    session.state["test_key"] = "test_value"
    await svc.update_session(session)
    session2 = await svc.get_session(app_name="app", user_id="user", session_id="s1")
    assert session2.state["test_key"] == "test_value"
