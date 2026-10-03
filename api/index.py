"""Vercel entry point: exposes the FastAPI app from backend/main.py as a serverless function."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app  # noqa: E402,F401
