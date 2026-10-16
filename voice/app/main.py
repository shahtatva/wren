from fastapi import FastAPI

app = FastAPI(title="Wren voice")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
