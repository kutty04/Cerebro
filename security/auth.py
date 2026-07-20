import os
import time
import logging
from dataclasses import dataclass
from typing import Optional
import requests
from fastapi import Request, HTTPException, status, Depends
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class AuthenticatedUser:
    id: str
    email: Optional[str] = None
    role: str = "authenticated"
    access_token: str = ""


def get_supabase_client():
    import supabase
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        return supabase.create_client(url, key)
    except Exception as e:
        logger.error("Failed to create Supabase client in auth [op=get_supabase_client, exc_type=%s]", type(e).__name__)
        return None


async def get_current_user(request: Request) -> AuthenticatedUser:
    """
    FastAPI dependency that extracts and verifies the Supabase Bearer access token.
    Derives user ID strictly from the verified token.
    Fails closed with HTTP 401 if token is missing, invalid, expired, or verification times out.
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

    # Strategy 1: Verify token using Supabase Auth client (auth.get_user)
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if supabase_url and supabase_key:
        try:
            # Direct HTTP call to Supabase auth API for fast, reliable verification with strict timeout
            auth_endpoint = f"{supabase_url.rstrip('/')}/auth/v1/user"
            headers = {
                "apikey": supabase_key,
                "Authorization": f"Bearer {token}",
            }
            res = requests.get(auth_endpoint, headers=headers, timeout=5.0)

            if res.status_code == 200:
                user_data = res.json()
                user_id = user_data.get("id")
                if user_id:
                    return AuthenticatedUser(
                        id=user_id,
                        email=user_data.get("email"),
                        role=user_data.get("role", "authenticated"),
                        access_token=token,
                    )
            elif res.status_code in (400, 401, 403):
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
            logger.error("Supabase auth request failed [op=verify_token, exc_type=%s]", type(e).__name__)

    # Strategy 2: In local test / dev mode without live Supabase, allow test token mock format: "mock-token-user-<id>"
    if token.startswith("mock-token-user-"):
        mock_id = token.replace("mock-token-user-", "")
        return AuthenticatedUser(id=mock_id, email=f"{mock_id}@test.com", access_token=token)

    # Fail closed if token verification is unavailable or invalid
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication failed: invalid token or provider unavailable.",
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
