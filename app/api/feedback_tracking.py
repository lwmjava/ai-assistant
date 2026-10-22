"""Chat HTTP adaptation preserves existing public errors and adds attempt references."""

import inspect
import json
from functools import wraps

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.api.errors import PublicHTTPError
from app.models.feedback import ChatAttempt
from app.services.chat_feedback import FeedbackAccessError, FeedbackConflictError, FeedbackService


def track_chat_response(method):
    @wraps(method)
    async def wrapped(*args, **kwargs):
        bound = inspect.signature(method).bind(*args, **kwargs)
        bound.apply_defaults()
        session, user = bound.arguments["session"], bound.arguments["current_user"]
        req = bound.arguments.get("req")
        if req is not None and not req.message.strip() and not req.attachments and not req.resume_message_id:
            return await method(*args, **kwargs)
        svc = FeedbackService(session)

        def references():
            row = session.get(ChatAttempt, session.info.get("feedback_attempt_id"))
            if row is None:
                return {}
            return {
                "attempt_id": row.id,
                "conversation_id": row.conversation_id,
                "user_message_id": row.user_message_id,
                "assistant_message_id": row.assistant_message_id,
            }

        try:
            response = await svc.track_call(
                user, lambda: method(*args, **kwargs), str(req.run_id) if req is not None and req.run_id else None
            )
        except FeedbackAccessError:
            raise HTTPException(404, "记录不存在") from None
        except FeedbackConflictError:
            raise HTTPException(409, "尝试编号已存在，请查询原尝试") from None
        except PublicHTTPError as exc:
            exc.extra.update(references())
            raise
        except HTTPException as exc:
            return JSONResponse(
                {"detail": exc.detail, **references()}, status_code=exc.status_code, headers=exc.headers
            )
        except TimeoutError:
            return JSONResponse(
                {"detail": "长时间没有输出，请稍后重试", "code": "no_response", **references()}, status_code=504
            )
        except Exception:
            return JSONResponse(
                {"detail": "生成失败，请稍后重试", "code": "api_error", **references()}, status_code=500
            )
        if isinstance(response, JSONResponse):
            return JSONResponse(
                {**json.loads(response.body), **references()},
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items() if k != "content-length"},
            )
        return response.model_copy(update=references())

    return wrapped
