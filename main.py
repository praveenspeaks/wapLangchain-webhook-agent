"""ASGI entry point. Run: uv run uvicorn main:app --reload."""

from app.application import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_config=None)
