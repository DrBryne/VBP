import os
import asyncio
import google.auth
from google import genai
from google.genai import types

async def test():
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "sunny-passage-362617")
    location = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = location

    print(f"Project: {project_id}, Location: {location}")

    client = genai.Client()
    uri = "gs://veiledende_behandlingsplan/ALS/250-254.pdf"
    mime_type = "application/octet-stream"
    
    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=[
                types.Part.from_uri(file_uri=uri, mime_type=mime_type),
                "Analyser den vedlagte artikkelen."
            ]
        )
        print("Success:", response.text)
    except Exception as e:
        import traceback
        traceback.print_exc()
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            print("Raw response:")
            print(e.response.text)
        elif hasattr(e, '__cause__') and e.__cause__:
            print(f"Cause: {e.__cause__}")

if __name__ == "__main__":
    asyncio.run(test())
