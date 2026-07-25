import os
import logging
from dataclasses import dataclass
from typing import Optional
import requests
from fastapi import Request, HTTPException, status
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class AuthenticatedUser:
    id: str
    email: Optional[str] = None
    role: str = "authenticated"
    access_token: str = ""


async def get_current_user(request: Request) -> AuthenticatedUser:
    """
    FastAPI dependency that extracts and verifies the Supabase Bearer access token.
    Derives user ID strictly from the verified Supabase token response.
    Fails closed with HTTP 401 if token is missing, invalid, expired, or verification fails.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = auth_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme. Expected Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty authentication token provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("Supabase credentials unconfigured during auth verification [op=verify_token]")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication provider unconfigured.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        auth_endpoint = f"{supabase_url.rstrip('/')}/auth/v1/user"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {token}",
        }
        res = requests.get(auth_endpoint, headers=headers, timeout=5.0)

        if res.status_code == 200:
            user_data = res.json()
            user_id = user_data.get("id") if isinstance(user_data, dict) else None
            if user_id and isinstance(user_id, str) and user_id.strip():
                return AuthenticatedUser(
                    id=user_id,
                    email=user_data.get("email"),
                    role=user_data.get("role", "authenticated"),
                    access_token=token,
                )
            else:
                logger.warning("Supabase auth response missing valid user ID [op=verify_token]")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid user profile in authentication token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        else:
            logger.warning("Supabase auth verification rejected token [op=verify_token, status=%d]", res.status_code)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired authentication token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        logger.error("Supabase auth verification timed out [op=verify_token]")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Authentication provider timed out.",
        )
    except Exception as e:
        logger.error("Supabase auth verification failed [op=verify_token, exc_type=%s]", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication verification failed.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_identity_match(authenticated_user_id: str, client_supplied_user_id: Optional[str]):
    """
    Enforces that client-supplied user_id (if present for backwards compatibility)
    matches the verified authenticated user ID. Raises HTTP 403 Forbidden on mismatch.
    """
    if client_supplied_user_id and client_supplied_user_id.strip():
        if client_supplied_user_id.strip() != authenticated_user_id:
            logger.warning("User identity mismatch attempt blocked [op=verify_identity]")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: user identity mismatch.",
            )
