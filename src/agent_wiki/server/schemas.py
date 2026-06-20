from pydantic import BaseModel


class ContextRequest(BaseModel):
    prompt: str


class IngestJSON(BaseModel):
    filename: str
    content: str
    topic: str | None = None
    tags: str | None = None     # comma-separated
    update: bool = False
    force: bool = False


class SyncRequest(BaseModel):
    source: str | None = None
    since: str | None = None
    dry_run: bool = False
    include_live: bool = False


class AdaptRequest(BaseModel):
    source: str
    ref: str
    output: str | None = None


class DoctorRequest(BaseModel):
    fix: bool = False
    dry_run: bool = False


class ReingestRequest(BaseModel):
    name: str
    force: bool = False
