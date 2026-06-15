"""
Token Info Middleware
====================
Attaches `token_info` metadata to every JSON response on the *service* routes
(/transcribe, /summary, ...).  Auth routes (/auth/*) are intentionally skipped.

The middleware NEVER rejects a request — it only annotates the response so the
client always knows the token state without having to decode the JWT itself.

Response additions (merged into every JSON body):
  {
    "token_info": {
      "status":     "valid" | "expired" | "invalid" | "missing",
      "detail":     human-readable reason string,
      "user_id":    "<uuid>"   | null,
      "email":      "<email>"  | null,
      "expires_at": "<iso8601-utc>" | null   (when the token expires/expired)
    }
  }
"""
import json
import logging
from datetime import datetime, timezone

from jose import ExpiredSignatureError, JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

logger = logging.getLogger(__name__)

# Routes that start with any of these prefixes are SKIPPED (auth handles itself)
_AUTH_PREFIXES = ("/auth",)

# Only these prefixes get the token_info annotation
_SERVICE_PREFIXES = ("/transcribe", "/summary")


def _inspect_token(authorization: str | None) -> dict:
    """
    Decode and inspect a raw Authorization header value.

    Returns a token_info dict — never raises.
    """
    if not authorization:
        return {
            "status": "missing",
            "detail": "No Authorization header provided.",
            "user_id": None,
            "email": None,
            "expires_at": None,
        }

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return {
            "status": "invalid",
            "detail": "Authorization header must be 'Bearer <token>'.",
            "user_id": None,
            "email": None,
            "expires_at": None,
        }

    raw_token = parts[1]

    try:
        # Decode with full validation (signature + expiry)
        payload = jwt.decode(
            raw_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )

        exp_ts = payload.get("exp")
        expires_at = (
            datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()
            if exp_ts
            else None
        )

        return {
            "status": "valid",
            "detail": "Access token is valid.",
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "expires_at": expires_at,
        }

    except ExpiredSignatureError:
        # Decode WITHOUT verification so we can still read the payload claims
        try:
            payload = jwt.decode(
                raw_token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
                options={"verify_exp": False},
            )
            exp_ts = payload.get("exp")
            expired_at = (
                datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()
                if exp_ts
                else None
            )
            return {
                "status": "expired",
                "detail": f"Access token expired at {expired_at}. Please refresh your token.",
                "user_id": payload.get("sub"),
                "email": payload.get("email"),
                "expires_at": expired_at,
            }
        except Exception:
            return {
                "status": "expired",
                "detail": "Access token has expired. Please refresh your token.",
                "user_id": None,
                "email": None,
                "expires_at": None,
            }

    except JWTError as exc:
        return {
            "status": "invalid",
            "detail": f"Token validation failed: {str(exc)}",
            "user_id": None,
            "email": None,
            "expires_at": None,
        }


class TokenInfoMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that appends `token_info` to JSON responses on
    service routes.  Auth routes are completely skipped.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip auth routes entirely — they manage their own token logic
        if any(path.startswith(prefix) for prefix in _AUTH_PREFIXES):
            return await call_next(request)

        # Only annotate service routes
        is_service_route = any(path.startswith(prefix) for prefix in _SERVICE_PREFIXES)

        # Always let the actual handler run first
        response = await call_next(request)

        if not is_service_route:
            return response

        # Only mutate JSON responses
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read the original response body
        body_bytes = b""
        async for chunk in response.body_iterator:
            body_bytes += chunk

        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Can't parse — return untouched
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=content_type,
            )

        # Build and inject token_info
        authorization = request.headers.get("authorization")
        token_info = _inspect_token(authorization)

        if isinstance(body, dict):
            body["token_info"] = token_info
        else:
            # body is a list or scalar — wrap it
            body = {"data": body, "token_info": token_info}

        new_body = json.dumps(body, default=str).encode("utf-8")

        # Rebuild response with updated Content-Length
        headers = dict(response.headers)
        headers["content-length"] = str(len(new_body))

        logger.debug(
            "TokenInfoMiddleware [%s %s] → token_status=%s user=%s",
            request.method,
            path,
            token_info["status"],
            token_info.get("email"),
        )

        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )
