import json
from google.cloud import aiplatform
from vertexai.preview import reasoning_engines
import vertexai

with open("deployment_metadata.json") as f:
    metadata = json.load(f)
    engine_id = metadata["remote_agent_engine_id"]

vertexai.init(location="us-central1")
client = vertexai.Client()
engine = client.agent_engines.get(name=engine_id)
print(f"Engine: {engine.name}")
try:
    # Actually, sessions are usually managed via a session service or we can just list the sessions for the app.
    # The ADK deploy guide says Agent Engine sessions are managed natively.
    # We might not be able to list all sessions easily without knowing the user_id.
    pass
except Exception as e:
    print(f"Error: {e}")
