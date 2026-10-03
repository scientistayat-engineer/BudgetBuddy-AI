// Where the BudgetBuddy AI backend (FastAPI) lives.
//  - "" (empty)  : same site. Use this when the frontend and API are deployed together on Vercel, or locally with `uvicorn backend.main:app`.
//  - a full URL  : use this on Firebase Hosting, e.g. "https://budgetbuddy-ai.vercel.app" (no trailing slash).
//    That URL must also be listed in ALLOWED_ORIGINS on the backend.
export const API_BASE = "";
