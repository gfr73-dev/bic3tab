import os
from pathlib import Path

from dotenv import load_dotenv
from waitress import serve

# A Windows service does not inherit the interactive user's shell environment.
load_dotenv(Path(__file__).resolve().with_name(".env"))

from app import app


if __name__ == "__main__":
    serve(
        app,
        host=os.getenv("BIC3TAB_HOST", "127.0.0.1"),
        port=int(os.getenv("BIC3TAB_PORT", "5800")),
        threads=int(os.getenv("WAITRESS_THREADS", "8")),
    )
