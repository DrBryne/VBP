import os
import sys
import uuid

import streamlit as st

# Add project root to sys.path so we can import shared configs
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import vertexai
from google.cloud import storage

from app.shared.config import config

# Set Vertex AI Location
vertexai.init(project=os.environ.get("GOOGLE_CLOUD_PROJECT"), location=config.PREVIEW_MODEL_LOCATION)

import pyrebase

# Configure Firebase / Identity Platform
firebase_config = {
    "apiKey": os.environ.get("FIREBASE_API_KEY", ""),
    "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", f"{os.environ.get('GOOGLE_CLOUD_PROJECT')}.firebaseapp.com"),
    "projectId": os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
    "storageBucket": f"{os.environ.get('GOOGLE_CLOUD_PROJECT')}.appspot.com",
    "messagingSenderId": "",
    "appId": "",
    "databaseURL": ""
}

# Only initialize if we have an API key (we'll set this during Cloud Run deployment)
if firebase_config["apiKey"]:
    firebase = pyrebase.initialize_app(firebase_config)
    auth = firebase.auth()

st.set_page_config(
    page_title="VBP Klinisk Syntese Chat",
    page_icon="🩺",
    layout="wide",
)

# Authentication State
if "user_token" not in st.session_state:
    st.session_state.user_token = None

if "user_email" not in st.session_state:
    st.session_state.user_email = None

def login():
    st.title("🩺 VBP Klinisk Syntese Innlogging")
    st.markdown("Denne applikasjonen krever autorisert tilgang. Vennligst logg inn nedenfor.")

    if not firebase_config["apiKey"]:
        st.warning("⚠️ Identity Platform er ikke fullstendig konfigurert. `FIREBASE_API_KEY` miljøvariabelen mangler.")
        return

    email = st.text_input("E-postadresse")
    password = st.text_input("Passord", type="password")

    if st.button("Logg inn"):
        try:
            user = auth.sign_in_with_email_and_password(email, password)
            st.session_state.user_token = user['idToken']
            st.session_state.user_email = email
            st.rerun()
        except Exception as e:
            st.error("Ugyldig e-post eller passord.")

if not st.session_state.user_token and firebase_config["apiKey"]:
    login()
    st.stop() # Halt rendering the rest of the app until logged in

st.title("🩺 VBP Klinisk Syntese Chat")

# Session state initialization
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state["messages"] = []

if "gcs_path" not in st.session_state:
    st.session_state["gcs_path"] = None

@st.cache_data(ttl=60)
def get_available_reports():
    """List available synthesis reports from GCS."""
    bucket_name = config.BASE_BUCKET.replace("gs://", "").split("/")[0]
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix="runs/")

        reports = []
        for blob in blobs:
            if blob.name.endswith("workflow_synthesis.json"):
                reports.append(f"gs://{bucket_name}/{blob.name}")
        # Sort newest first assuming name has timestamp
        return sorted(reports, reverse=True)
    except Exception as e:
        st.error(f"Failed to list reports from GCS: {e}")
        return []

# Sidebar for GCS Selection
with st.sidebar:
    st.header("1. Velg Synteserapport")
    st.markdown("Velg en generert rapport fra Google Cloud Storage for å spørre om.")
    reports = get_available_reports()

    if reports:
        selected_report = st.selectbox(
            "Tilgjengelige Rapporter:",
            reports,
            format_func=lambda x: x.split("runs/")[1].split("/")[0] if "runs/" in x else x
        )
        if selected_report != st.session_state["gcs_path"]:
            st.session_state["gcs_path"] = selected_report
            # Clear chat when a new report is selected
            st.session_state["messages"] = [
                {"role": "assistant", "content": f"Hei! Jeg er klar til å svare på spørsmål om rapporten: {selected_report.split('/')[-2]}."}
            ]
    else:
        st.warning("Ingen rapporter funnet i GCS-bøtten.")

    st.divider()
    st.markdown("### Brukeridentitet")
    # For local testing, mock identity. In Cloud Run + IAP, fetch from headers.
    iap_user = st.context.headers.get("X-Goog-Authenticated-User-Email")
    # Use Identity Platform logged in email if present
    if st.session_state.get("user_email"):
        user_email = st.session_state.user_email
    elif iap_user:
        user_email = iap_user.replace("accounts.google.com:", "")
    else:
        user_email = "local-developer@example.com"

    st.text(f"Logget inn som: {user_email}")

# Get Engine ID from deployment_metadata.json
def get_engine_id():
    import json
    metadata_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
    if os.path.exists(metadata_path):
        with open(metadata_path) as f:
            return json.load(f).get("remote_agent_engine_id")
    return None

engine_id = get_engine_id()

if not engine_id:
    st.warning("⚠️ Agent Engine er ikke distribuert. Vennligst kjør `make deploy` for å teste chat-grensesnittet.")
    st.stop()

# Display chat messages
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat Input
if prompt := st.chat_input("Still et spørsmål om den kliniske syntesen..."):
    if not st.session_state["gcs_path"]:
        st.error("Vennligst velg en rapport fra sidepanelet først.")
        st.stop()

    # Append user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Prepend [CHAT] command for the RouterAgent
    # We also provide the gcs_path context in the message so the agent knows what to read
    formatted_prompt = f"[CHAT] I am asking about report: {st.session_state['gcs_path']}\n\nUser Question: {prompt}"

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_response = ""

        try:
            # Initialize client
            client = vertexai.Client(location=config.PREVIEW_MODEL_LOCATION)
            agent = client.agent_engines.get(name=engine_id)

            # Ensure the session is created on the backend (ADK 1.15.0+ requirement for persistent session IDs)
            if getattr(st.session_state, "_session_created", False) is False:
                try:
                    agent.create_session(user_id=user_email, session_id=st.session_state["session_id"])
                    st.session_state._session_created = True
                except Exception:
                    pass

            response_stream = agent.stream_query(
                user_id=user_email,
                session_id=st.session_state["session_id"],
                message=formatted_prompt
            )

            for chunk in response_stream:
                # AgentEngine returns a stream of events as dictionaries
                if isinstance(chunk, dict):
                    # Check if it's from the assistant/agent
                    if chunk.get("author") == "report_chat" or chunk.get("author") == "router":
                        content = chunk.get("content", {})
                        parts = content.get("parts", [])
                        if parts:
                            text_chunk = parts[0].get("text", "")
                            full_response += text_chunk
                            message_placeholder.markdown(full_response + "▌")
                elif hasattr(chunk, "message") and chunk.message and chunk.message.parts:
                    # Fallback for SDK objects if it parses them natively
                    text_chunk = chunk.message.parts[0].text
                    full_response += text_chunk
                    message_placeholder.markdown(full_response + "▌")

            message_placeholder.markdown(full_response)

            # Save to history
            st.session_state.messages.append({"role": "assistant", "content": full_response})

        except Exception as e:
            st.error(f"Error querying Agent Engine: {e}")
