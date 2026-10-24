import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import allowed_origins
from app.auth_routes import router as auth_router
from app.routes import router

# The app's own INFO lines (one per turn, one per retrieval) next to uvicorn's.
_app_log = logging.getLogger("app")
if not _app_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _app_log.addHandler(_handler)
    _app_log.setLevel(logging.INFO)

app = FastAPI(title="Wren voice")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins()),
    allow_methods=["POST"],
    allow_headers=["content-type", "authorization"],
)
app.include_router(router)
app.include_router(auth_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
