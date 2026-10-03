"""BudgetBuddy AI backend.

A small FastAPI service that sits between the browser and Groq:

    Browser (Firebase user)  --ID token-->  FastAPI  --Groq key-->  Groq API

Why it exists
-------------
* The Groq API key lives only on the server (environment variable), never in the
  browser, in Firestore or in the repository.
* Every request must carry a valid Firebase ID token, so only signed-in users of
  the app can spend the Groq quota.
* Requests are validated (roles, sizes, number of messages) and rate limited.

Endpoints
---------
GET  /api/health   status check (is the AI key configured, is auth enforced)
POST /api/ai       chat completion through Groq -> {"content": "..."}

Run locally (from the project root):
    uvicorn backend.main:app --reload
This also serves the frontend at http://localhost:8000 when not on Vercel.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
MAX_TOKENS = 700
RATE_LIMIT = 30          # requests ...
RATE_WINDOW = 60         # ... per this many seconds, per user
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Tests replace this with an httpx.MockTransport so no real network call is made.
_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None
_hits: dict[str, deque] = defaultdict(deque)


# --------------------------------------------------------------------------- #
# Settings (read at call time so they can be changed without a restart in tests)
# --------------------------------------------------------------------------- #
def groq_key() -> str:
    return os.getenv("GROQ_API_KEY", "").strip()


def groq_model() -> str:
    return os.getenv("GROQ_MODEL", "").strip() or DEFAULT_MODEL


def firebase_project() -> str:
    return os.getenv("FIREBASE_PROJECT_ID", "").strip()


def allowed_origins() -> list[str]:
    raw = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://localhost:5000",
    )
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class AIRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=6)
    json_mode: bool = False


class AIResponse(BaseModel):
    content: str


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
app = FastAPI(title="BudgetBuddy AI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


# --------------------------------------------------------------------------- #
# Authentication: Firebase ID token
# --------------------------------------------------------------------------- #
def verify_token(token: str, project_id: str) -> dict:
    """Validate a Firebase ID token and return its claims. Raises ValueError if invalid."""
    import cachecontrol
    import google.auth.transport.requests
    import requests
    from google.oauth2 import id_token

    session = cachecontrol.CacheControl(requests.session())  # caches Google's public certs
    request = google.auth.transport.requests.Request(session=session)
    return id_token.verify_firebase_token(token, request, audience=project_id)


def current_user(authorization: Optional[str] = Header(default=None)) -> str:
    """Return the Firebase uid of the caller (or 'anonymous' if auth is not enforced)."""
    project = firebase_project()
    if not project:
        return "anonymous"  # local development without Firebase
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Please sign in again to use the AI features.")
    try:
        claims = verify_token(authorization.split(" ", 1)[1].strip(), project)
    except Exception:
        raise HTTPException(401, "Your sign-in has expired. Please sign in again.")
    uid = claims.get("user_id") or claims.get("sub")
    if not uid:
        raise HTTPException(401, "Invalid sign-in token.")
    return str(uid)


def check_rate_limit(uid: str) -> None:
    """Best-effort sliding window. On serverless hosts each instance counts separately."""
    now = time.monotonic()
    q = _hits[uid]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, "Too many AI requests. Please wait a minute and try again.")
    q.append(now)


# --------------------------------------------------------------------------- #
# Groq call
# --------------------------------------------------------------------------- #
async def call_groq(req: AIRequest) -> str:
    key = groq_key()
    if not key:
        raise HTTPException(503, "AI is not configured on the server (GROQ_API_KEY is missing).")

    payload: dict = {
        "model": groq_model(),
        "messages": [m.model_dump() for m in req.messages],
        "temperature": 0 if req.json_mode else 0.4,
        "max_tokens": MAX_TOKENS,
    }
    if req.json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=30, transport=_TRANSPORT) as client:
            r = await client.post(GROQ_URL, json=payload, headers={"Authorization": f"Bearer {key}"})
    except httpx.TimeoutException:
        raise HTTPException(504, "The AI service took too long to answer. Please try again.")
    except httpx.HTTPError:
        raise HTTPException(502, "Couldn't reach the AI service. Please try again.")

    if r.status_code == 429:
        raise HTTPException(429, "Groq is busy or the free limit was reached. Try again in a minute.")
    if r.status_code in (401, 403):
        raise HTTPException(502, "The server's AI key was rejected. Contact the app owner.")
    if r.status_code in (400, 404):
        raise HTTPException(502, "The AI model name is invalid. Check GROQ_MODEL on the server.")
    if r.status_code >= 400:
        raise HTTPException(502, f"The AI service returned an error ({r.status_code}).")

    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise HTTPException(502, "The AI service returned an unexpected answer.")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "ai_configured": bool(groq_key()),
        "auth_required": bool(firebase_project()),
        "model": groq_model(),
    }


@app.post("/api/ai", response_model=AIResponse)
async def ai(req: AIRequest, uid: str = Depends(current_user)) -> AIResponse:
    check_rate_limit(uid)
    return AIResponse(content=await call_groq(req))


# Local development: serve the frontend from the same server (not used on Vercel,
# where static files are served by the platform).
if FRONTEND_DIR.is_dir() and not os.getenv("VERCEL"):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
