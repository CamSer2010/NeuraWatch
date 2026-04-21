"""GET /health — system liveness + model load state.

Used by the frontend `model-loading` UI state (NW-1205) and by the
ngrok deploy verification step (NW-1504).
"""
from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict:
    return {
        "status": "healthy",
        "model_loaded": bool(getattr(request.app.state, "model_loaded", False)),
    }
