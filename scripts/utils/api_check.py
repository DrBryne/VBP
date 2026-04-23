import asyncio
import os

from google import genai


async def test():
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "sunny-passage-362617")
    location = "us-central1"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = location

    print(f"Project: {project_id}, Location: {location}")

    client = genai.Client()
    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents="Hello"
        )
        print("Success:", response.text)
    except Exception:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
