import asyncio
import json
import os

import aiohttp
from google import genai
from google.genai import types

# 1. Monkeypatch aiohttp to capture the raw response text
original_json = aiohttp.ClientResponse.json

async def patched_json(self, *args, **kwargs):
    text = await self.text()
    try:
        return json.loads(text)
    except Exception as e:
        print("\n" + "="*50)
        print("🚨 RAW HTTP RESPONSE BODY CAPTURED 🚨")
        print("HTTP STATUS:", self.status)
        print("URL:", self.url)
        print("-" * 50)
        print(text)
        print("="*50 + "\n")
        raise e

aiohttp.ClientResponse.json = patched_json

async def run_diagnostic():
    # 2. Setup the exact environment the agent uses
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "sunny-passage-362617")
    location = "global"

    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_LOCATION"] = location
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id

    client = genai.Client()

    uri = "gs://veiledende_behandlingsplan/ALS/250-254.pdf"
    target_group = "ALS - Amytrofisk lateral sklerose"

    # 3. Replicate the payload
    parts = [
        types.Part.from_uri(file_uri=uri, mime_type="application/pdf"),
        types.Part.from_text(text=f"Bruksområde: {target_group}\n\nAnalyser den vedlagte artikkelen.")
    ]

    config = types.GenerateContentConfig(
        temperature=1.0,
        thinking_config=types.ThinkingConfig(
            include_thoughts=True,
            thinking_level="high"
        )
    )

    print("Calling Gemini API directly via SDK...")
    print("Model: gemini-3.1-pro-preview")
    print(f"Location: {location}")
    print(f"URI: {uri}")

    # 4. Execute the call
    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=parts,
            config=config
        )
        print("Success! Response received.")
    except Exception as e:
        print(f"SDK Call Failed: {type(e).__name__} - {e}")

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
