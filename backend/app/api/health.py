"""GET /health — system liveness + model load state.

Used by the frontend `model-loading` UI state (NW-1205) and by the
ngrok deploy verification step (NW-1504).
"""
from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict:
    svc = getattr(request.app.state, "inference_service", None)
    return {
        "status": "healthy",
        "model_loaded": bool(svc and svc.is_loaded),
    }
