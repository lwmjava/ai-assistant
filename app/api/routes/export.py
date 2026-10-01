"""导出租户对话。只接受 POST，避免对话原文出现在 URL 上。"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services.export_service import (
    ExportAuditError,
    ExportForbiddenError,
    ExportTooLargeError,
    export_tenant_conversations,
    iter_export_bytes,
)

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/conversations")
async def export_conversations(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """当前租户的租户管理员下载本租户对话。审计写入成功后才开始返回文件。"""
    raw_body = await request.body()
    if raw_body.strip():
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体不是 JSON")
        if isinstance(data, dict) and "tenant_id" in data and data["tenant_id"] != current_user.tenant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权导出")
    try:
        raw = export_tenant_conversations(
            session, user_id=current_user.id, tenant_id=current_user.tenant_id
        )
    except ExportForbiddenError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权导出")
    except ExportTooLargeError:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"code": "export_too_large"},
        )
    except ExportAuditError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"code": "export_audit_failed"},
        )
    return StreamingResponse(
        iter_export_bytes(raw),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="conversations.json"'},
    )
