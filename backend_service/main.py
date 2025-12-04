import uvicorn

from backend_service.app import app


def run() -> None:
    uvicorn.run(
        "backend_service.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    run()


