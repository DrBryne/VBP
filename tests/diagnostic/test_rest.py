import os
import httpx
import google.auth
from google.auth.transport.requests import Request

def get_token():
    credentials, project = google.auth.default()
    credentials.refresh(Request())
    return credentials.token, project

project_id = "sunny-passage-362617"
location = "global"
model = "gemini-3.1-pro-preview"

token, _ = get_token()

url = f"https://aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/{location}/publishers/google/models/{model}:generateContent"

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

payload = {
    "contents": [{
        "role": "user",
        "parts": [
            {"fileData": {"fileUri": "gs://veiledende_behandlingsplan/ALS/250-254.pdf", "mimeType": "application/xml"}},
            {"text": "Analyser den vedlagte artikkelen."}
        ]
    }]
}

response = httpx.post(url, headers=headers, json=payload)
print(f"Status Code: {response.status_code}")
print(f"Response Body: {response.text}")
