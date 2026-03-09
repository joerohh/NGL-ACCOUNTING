"""Auth endpoints — login, logout, current user, and user management."""

import logging
from typing import Optional

import jwt
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from config import AUTH_TOKEN

logger = logging.getLogger("ngl.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# JWT secret — use the existing AUTH_TOKEN as the signing key (local-only)
JWT_SECRET = AUTH_TOKEN
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72  # 3-day sessions


# ── Models ──

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    displayName: Optional[str] = ""
    role: Optional[str] = "operator"


class UpdateUserRequest(BaseModel):
    displayName: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    password: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    currentPassword: str
    newPassword: str


# ── Helpers ──

def create_jwt(user: dict) -> str:
    """Create a JWT token for an authenticated user."""
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> Optional[dict]:
    """Decode and validate a JWT. Returns payload or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_current_user(request: Request) -> Optional[dict]:
    """Extract current user from request (set by middleware). Returns None if not authenticated."""
    return getattr(request.state, "user", None)


# ── Endpoints ──

@router.get("/token")
async def get_auth_token():
    """Bootstrap endpoint — tells the frontend whether login is required.

    Returns loginRequired: False when auth middleware is disabled (local dev),
    or loginRequired: True when auth middleware is active.
    """
    from config import AUTH_ENABLED
    return {"token": None, "loginRequired": AUTH_ENABLED}


@router.post("/login")
async def login(data: LoginRequest):
    """Authenticate with username + password. Returns JWT token + user info."""
    from services.database import authenticate_user
    user = authenticate_user(data.username, data.password)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    token = create_jwt(user)
    logger.info("User logged in: %s (role: %s)", user["username"], user["role"])
    return {"token": token, "user": user}


@router.get("/me")
async def get_me(request: Request):
    """Return the currently authenticated user."""
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    # Refresh user data from DB
    from services.database import get_user_by_id
    fresh = get_user_by_id(int(user["sub"]))
    if not fresh:
        return JSONResponse(status_code=401, content={"detail": "User not found"})
    return {"user": fresh}


@router.post("/change-password")
async def change_password(data: ChangePasswordRequest, request: Request):
    """Change the current user's password."""
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    from services.database import authenticate_user, update_user
    # Verify current password
    check = authenticate_user(user["username"], data.currentPassword)
    if not check:
        return JSONResponse(status_code=400, content={"detail": "Current password is incorrect"})

    update_user(int(user["sub"]), {"password": data.newPassword})
    logger.info("User changed password: %s", user["username"])
    return {"status": "ok", "message": "Password changed successfully"}


# ── User Management (admin only) ──

def _require_admin(request: Request):
    from config import AUTH_ENABLED
    user = get_current_user(request)
    # When auth is disabled, treat all requests as admin
    if not AUTH_ENABLED:
        return {"sub": "0", "username": "local", "role": "admin"}, None
    if not user:
        return None, JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    if user.get("role") != "admin":
        return None, JSONResponse(status_code=403, content={"detail": "Admin access required"})
    return user, None


@router.get("/users")
async def list_all_users(request: Request):
    """List all users (admin only)."""
    _, err = _require_admin(request)
    if err:
        return err
    from services.database import list_users
    users = list_users(active_only=False)
    return {"users": users}


@router.post("/users")
async def create_new_user(data: CreateUserRequest, request: Request):
    """Create a new user (admin only)."""
    _, err = _require_admin(request)
    if err:
        return err

    if not data.username.strip():
        return JSONResponse(status_code=400, content={"detail": "Username is required"})
    if len(data.password) < 4:
        return JSONResponse(status_code=400, content={"detail": "Password must be at least 4 characters"})
    if data.role not in ("admin", "operator"):
        return JSONResponse(status_code=400, content={"detail": "Role must be 'admin' or 'operator'"})

    from services.database import create_user
    try:
        user = create_user(data.username, data.password, data.displayName or "", data.role)
    except ValueError as e:
        return JSONResponse(status_code=409, content={"detail": str(e)})

    logger.info("User created: %s (role: %s)", user["username"], user["role"])
    return user


@router.put("/users/{user_id}")
async def update_existing_user(user_id: int, data: UpdateUserRequest, request: Request):
    """Update a user (admin only)."""
    admin, err = _require_admin(request)
    if err:
        return err

    from services.database import update_user
    update_data = {}
    if data.displayName is not None:
        update_data["displayName"] = data.displayName
    if data.role is not None:
        update_data["role"] = data.role
    if data.active is not None:
        update_data["active"] = data.active
    if data.password is not None:
        if len(data.password) < 4:
            return JSONResponse(status_code=400, content={"detail": "Password must be at least 4 characters"})
        update_data["password"] = data.password

    user = update_user(user_id, update_data)
    if not user:
        return JSONResponse(status_code=404, content={"detail": "User not found"})

    logger.info("User updated by %s: id=%d", admin["username"], user_id)
    return user


@router.delete("/users/{user_id}")
async def deactivate_user(user_id: int, request: Request):
    """Deactivate a user (admin only). Cannot deactivate yourself."""
    admin, err = _require_admin(request)
    if err:
        return err

    if int(admin["sub"]) == user_id:
        return JSONResponse(status_code=400, content={"detail": "Cannot deactivate your own account"})

    from services.database import delete_user
    ok = delete_user(user_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "User not found"})

    logger.info("User deactivated by %s: id=%d", admin["username"], user_id)
    return {"status": "deactivated", "userId": user_id}
