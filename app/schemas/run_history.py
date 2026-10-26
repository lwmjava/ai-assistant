from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RunEventOut(BaseModel):
    type: str
    name: str
    offset_ms: float
    duration_ms: float | None = None
    status: str | None = None
    round: int | None = None


class RunHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    status: str
    error_code: str | None
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    llm_call_count: int
    tool_call_count: int
    input_tokens: int | None
    output_tokens: int | None
    usage_source: str | None


class RunHistoryDetail(RunHistoryOut):
    events: list[RunEventOut]


class RunHistoryPage(BaseModel):
    items: list[RunHistoryOut]
    total: int
    page: int
    page_size: int
