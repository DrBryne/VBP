import vertexai
from vertexai.preview import reasoning_engines
import inspect
from app.shared.config import config

vertexai.init(project=config.PROJECT_ID, location=config.DEPLOYMENT_LOCATION)
# Create a dummy or get existing
with open("deployment_metadata.json") as f:
    import json
    meta = json.load(f)
    engine_id = meta["remote_agent_engine_id"]

engine = reasoning_engines.ReasoningEngine(engine_id)
print(f"Type: {type(engine)}")
print("Methods:")
for name, member in inspect.getmembers(engine):
    if not name.startswith("_"):
        print(f" - {name}")
