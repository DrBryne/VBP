import json
from unittest.mock import patch, MagicMock

import pytest
from google.adk.events import Event
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent
from app.agents.report_chat.tools import read_synthesis_report

@pytest.mark.asyncio
async def test_router_delegates_to_chat_via_prefix():
    """Test that the RootRouter delegates to the ReportChatAgent when [CHAT] prefix is found."""
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="vbp_workflow", user_id="test_user", session_id="test_session")
    session = await session_service.get_session(app_name="vbp_workflow", user_id="test_user", session_id="test_session")
    
    ctx = InvocationContext(
        session_service=session_service,
        session=session,
        invocation_id="test_inv",
        agent=root_agent,
        user_content=types.Content(role="user", parts=[types.Part.from_text(text="[CHAT] I am asking about report: gs://test/test.json\n\nWhat is this?")]),
        run_config=None
    )
    
    # We patch the chat agent's _run_async_impl to not invoke the actual LLM
    with patch("google.adk.agents.llm_agent.LlmAgent._run_async_impl") as mock_chat_run:
        async def mock_run_gen(*args, **kwargs):
            yield Event(author="report_chat", content=types.Content(parts=[types.Part.from_text(text="Chat response")]))
        
        mock_chat_run.side_effect = mock_run_gen
        
        events = []
        async for ev in root_agent._run_async_impl(ctx):
            events.append(ev)
            
        # Verify state was updated
        assert session.state.get("mode") == "chat"
        # Verify prefix was stripped
        assert ctx.user_content.parts[0].text == "I am asking about report: gs://test/test.json\n\nWhat is this?"
        # Verify delegation occurred
        mock_chat_run.assert_called_once()
        assert len(events) == 1
        assert events[0].author == "report_chat"

@pytest.mark.asyncio
async def test_router_delegates_to_chat_via_state():
    """Test that the RootRouter delegates to the ReportChatAgent when mode is 'chat'."""
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="vbp_workflow", user_id="test_user", session_id="test_session")
    session = await session_service.get_session(app_name="vbp_workflow", user_id="test_user", session_id="test_session")
    session.state["mode"] = "chat"
    
    ctx = InvocationContext(
        session_service=session_service,
        session=session,
        invocation_id="test_inv",
        agent=root_agent,
        user_content=types.Content(role="user", parts=[types.Part.from_text(text="What is this?")]),
        run_config=None
    )
    
    # We patch the chat agent's _run_async_impl to not invoke the actual LLM
    with patch("google.adk.agents.llm_agent.LlmAgent._run_async_impl") as mock_chat_run:
        async def mock_run_gen(*args, **kwargs):
            yield Event(author="report_chat", content=types.Content(parts=[types.Part.from_text(text="Chat response 2")]))
        
        mock_chat_run.side_effect = mock_run_gen
        
        events = []
        async for ev in root_agent._run_async_impl(ctx):
            events.append(ev)
            
        mock_chat_run.assert_called_once()
        assert len(events) == 1
        assert events[0].author == "report_chat"

@pytest.mark.asyncio
@patch("app.agents.report_chat.tools.storage.Client")
async def test_read_synthesis_report_tool(mock_storage_client):
    """Test the read_synthesis_report tool with a mocked GCS payload."""
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    
    mock_storage_client.return_value.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob
    
    mock_blob.exists.return_value = True
    
    # Mock a typical VBP JSON manifest
    sample_json = {
        "target_group": "ALS",
        "final_groups": [
            {"nursing_diagnosis": "Dysfagi", "goal": "Trygg svelging"},
            {"nursing_diagnosis": "Smerte", "goal": "Smertelindring"}
        ]
    }
    mock_blob.download_as_string.return_value = json.dumps(sample_json)
    
    # Test without query
    result1 = await read_synthesis_report("gs://my-bucket/path/workflow_synthesis.json")
    assert result1["status"] == "success"
    assert len(result1["data"]["final_groups"]) == 2
    
    # Test with query
    result2 = await read_synthesis_report("gs://my-bucket/path/workflow_synthesis.json", query="Dysfagi")
    assert result2["status"] == "success"
    assert len(result2["data"]["final_groups"]) == 1
    assert result2["data"]["final_groups"][0]["nursing_diagnosis"] == "Dysfagi"
    
    # Test with non-matching query
    result3 = await read_synthesis_report("gs://my-bucket/path/workflow_synthesis.json", query="Angst")
    assert result3["status"] == "success"
    assert "No groups matched query" in result3["message"]
