"""Malformed signed claims must fail through the public authentication boundary."""

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.main import app


@pytest.mark.parametrize("claim", ["exp", "nbf", "iat"])
@pytest.mark.parametrize("value", [None, [], {}])
def test_invalid_time_claim_returns_unauthorized(claim, value):
    token = jwt.encode({"sub": "synthetic-user", "type": "access", claim: value},
                       security.JWT_SECRET_KEY, algorithm=security.JWT_ALGORITHM)
    with pytest.raises(jwt.PyJWTError):
        security.decode_token(token)
    response = TestClient(app).get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert token not in response.text
