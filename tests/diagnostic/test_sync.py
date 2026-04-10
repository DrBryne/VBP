import os

from google import genai

project_id = "sunny-passage-362617"
location = "us-central1"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = location

client = genai.Client()
try:
    response = client.models.generate_content(
        model="gemini-3.1-pro-preview",
        contents="Hello"
    )
    print("Success:", response.text)
except Exception as e:
    print(f"Exception: {e}")
