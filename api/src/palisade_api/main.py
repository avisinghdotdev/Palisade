from importlib.metadata import version
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Palisade API", version=version("palisade-api"))


class Health(BaseModel):
    status: Literal["ok"]
    version: str


@app.get("/api/health", operation_id="health")
def health() -> Health:
    return Health(status="ok", version=app.version)
