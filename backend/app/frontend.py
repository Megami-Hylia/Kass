"""Optional same-origin static frontend for the personal desktop launcher."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles


class FrontendFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        # Even a file called api/... in a bundle must never shadow unknown API URLs.
        normalized = path.replace("\\", "/").lstrip("/").casefold()
        if normalized == "api" or normalized.startswith("api/"):
            raise HTTPException(404, "Endpoint API non trovato.")
        return await super().get_response(path, scope)


def mount_frontend(app: FastAPI, directory: Path | None):
    if directory is not None and directory.is_dir():
        app.mount("/", FrontendFiles(directory=directory, html=True), name="frontend")
