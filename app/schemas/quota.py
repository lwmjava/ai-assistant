"""租户配额的读取和修改。"""

from pydantic import BaseModel, Field, field_validator


class QuotaOut(BaseModel):
    """当前上限。空值表示该维度不限制。"""

    tenant_id: str
    message_limit: int | None
    storage_limit_bytes: int | None


class QuotaUpdate(BaseModel):
    """一次写全两个上限。空值表示不限制，0 表示不能再新增。"""

    model_config = {"extra": "forbid"}

    message_limit: int | None
    storage_limit_bytes: int | None
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("message_limit", "storage_limit_bytes")
    @classmethod
    def reject_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("上限不能为负数")
        return value

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("变更原因不能为空")
        if len(stripped) > 200:
            raise ValueError("变更原因不能超过 200 字")
        return stripped
