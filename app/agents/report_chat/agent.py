import os

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from app.agents.report_chat.tools import read_synthesis_report
from app.shared.config import config

instruction = """
You are the VBP Report Chat Agent, an expert clinical assistant. Your sole purpose is to answer questions based on the generated VBP synthesis report (workflow_synthesis.json).
You will receive a Google Cloud Storage (GCS) path to the specific report JSON from the user's initial state or message. 

When a user asks a question:
1. Always use the `read_synthesis_report` tool to fetch data from the provided GCS path.
2. Provide a precise, evidence-based answer utilizing ONLY the findings inside the JSON.
3. If the JSON does not contain the answer, explicitly state that you cannot find it in the current synthesis. 
4. Include quotes or references from the `evidence_snippets` section to back up your claims if present.
5. Format your responses with clear headings and bullet points where appropriate for readability.
"""

def create_report_chat_agent() -> Agent:
    return Agent(
        name="report_chat",
        model="gemini-3-flash-preview",
        instruction=instruction,
        description="Handles user queries about a specific clinical synthesis report.",
        tools=[read_synthesis_report]
    )
