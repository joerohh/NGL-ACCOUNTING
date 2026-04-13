"""Auth endpoints — login, logout, current user, and user management."""

import logging
from typing import Optional

import jwt
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

logger = logging.getLogger("ngl.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# JWT config — secret is loaded from DB on first use (persistent per installation)
_jwt_secret: Optional[str] = None
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72          # default session (no "remember me")
JWT_EXPIRE_HOURS_REMEMBER = 720  # 30-day session ("remember me" checked)


def _get_jwt_secret() -> str:
    """Lazy-load the JWT secret from the database (generated on first run)."""
    global _jwt_secret
    if _jwt_secret is None:
        from services.database import get_or_create_jwt_secret
        _jwt_secret = get_or_create_jwt_secret()
    return _jwt_secret


# ── Models ──

class LoginRequest(BaseModel):
    username: str
    password: str
    remember: Optional[bool] = False


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


class SetupRequest(BaseModel):
    username: str
    password: str
    displayName: Optional[str] = ""


class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token from Sign In with Google
    remember: Optional[bool] = False


# ── Helpers ──

def create_jwt(user: dict, remember: bool = False) -> str:
    """Create a JWT token for an authenticated user."""
    hours = JWT_EXPIRE_HOURS_REMEMBER if remember else JWT_EXPIRE_HOURS
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> Optional[dict]:
    """Decode and validate a JWT. Returns payload or None."""
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
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


@router.get("/setup-required")
async def setup_required():
    """Check if first-run setup is needed (no users exist yet)."""
    from services.database import get_user_count
    return {"setupRequired": get_user_count() == 0}


@router.post("/setup")
async def first_run_setup(data: SetupRequest):
    """Create the initial admin account. Only works when no users exist."""
    from services.database import get_user_count, create_user
    if get_user_count() > 0:
        return JSONResponse(status_code=400, content={"detail": "Setup already completed"})

    if not data.username.strip():
        return JSONResponse(status_code=400, content={"detail": "Username is required"})
    if len(data.password) < 4:
        return JSONResponse(status_code=400, content={"detail": "Password must be at least 4 characters"})

    user = create_user(data.username.strip(), data.password, data.displayName or data.username.strip(), "admin")
    logger.info("First-run setup: admin user '%s' created", user["username"])
    return {"status": "ok", "user": user}


@router.post("/login")
async def login(data: LoginRequest):
    """Authenticate with username + password. Returns JWT token + user info."""
    from services.database import authenticate_user
    user = authenticate_user(data.username, data.password)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    token = create_jwt(user, remember=data.remember)
    logger.info("User logged in: %s (role: %s)", user["username"], user["role"])
    return {"token": token, "user": user}


@router.get("/google/available")
async def google_available():
    """Check if Google login is configured."""
    from config import GOOGLE_CLIENT_ID
    return {"available": bool(GOOGLE_CLIENT_ID)}


@router.get("/google/login")
async def google_login_redirect():
    """Redirect to Google's OAuth authorization page."""
    import secrets as _secrets
    from config import GOOGLE_CLIENT_ID
    from starlette.responses import RedirectResponse

    if not GOOGLE_CLIENT_ID:
        return JSONResponse(status_code=400, content={"detail": "Google login not configured"})

    state = _secrets.token_urlsafe(32)
    # Store state for CSRF validation
    from services.database import set_setting
    set_setting("google_oauth_state", state)

    params = (
        f"client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri=http://localhost:8787/auth/google/callback"
        f"&response_type=code"
        f"&scope=openid%20email%20profile"
        f"&state={state}"
        f"&access_type=online"
        f"&prompt=select_account"
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@router.get("/google/callback")
async def google_callback(code: str = "", state: str = ""):
    """Handle Google OAuth redirect — exchange code for user info."""
    import httpx
    from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    from starlette.responses import HTMLResponse

    if not code:
        return HTMLResponse("<h3>Authorization failed — no code received.</h3>")

    # Validate CSRF state
    from services.database import get_setting, set_setting
    saved_state = get_setting("google_oauth_state")
    if not saved_state or state != saved_state:
        return HTMLResponse("<h3>Authorization failed — invalid state.</h3>")
    set_setting("google_oauth_state", "")  # Clear after use

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as client:
            token_resp = await client.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": "http://localhost:8787/auth/google/callback",
                "grant_type": "authorization_code",
            })
            if token_resp.status_code != 200:
                logger.error("Google token exchange failed: %s", token_resp.text)
                return HTMLResponse("<h3>Authorization failed — could not exchange code.</h3>")
            tokens = token_resp.json()

            # Get user info
            userinfo_resp = await client.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={
                "Authorization": f"Bearer {tokens['access_token']}"
            })
            if userinfo_resp.status_code != 200:
                return HTMLResponse("<h3>Authorization failed — could not fetch user info.</h3>")
            userinfo = userinfo_resp.json()
    except Exception as e:
        logger.error("Google OAuth error: %s", e)
        return HTMLResponse(f"<h3>Authorization failed — {e}</h3>")

    email = userinfo.get("email", "")
    name = userinfo.get("name", "") or email.split("@")[0]

    if not email:
        return HTMLResponse("<h3>Authorization failed — no email from Google.</h3>")

    # Only allow @ngltrans.net email addresses
    if not email.lower().endswith("@ngltrans.net"):
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
                <h2 style="color:#dc2626;">Access Denied</h2>
                <p>Only @ngltrans.net email addresses can sign in.</p>
                <script>setTimeout(() => window.close(), 5000);</script>
            </body></html>
        """)

    # Find or create user
    from services.database import get_user_by_username, create_google_user
    user = get_user_by_username(email)
    if not user:
        user = create_google_user(email, name)
        logger.info("Google login: created new operator account for %s", email)
    elif not user.get("active"):
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
                <h2 style="color:#dc2626;">Account Deactivated</h2>
                <p>Contact your admin to reactivate your account.</p>
                <script>setTimeout(() => window.close(), 5000);</script>
            </body></html>
        """)
    else:
        logger.info("Google login: %s (role: %s)", email, user["role"])

    # Create JWT and store it so the frontend can pick it up via polling
    token = create_jwt(user, remember=True)
    set_setting("google_pending_auth", f"{token}|||{user['id']}")

    return HTMLResponse("""
        <html><body style="font-family:Inter,sans-serif;text-align:center;padding:60px;">
            <h2 style="color:#16a34a;">Signed in!</h2>
            <p style="color:#64748b;">You can close this tab and return to the app.</p>
            <script>setTimeout(() => window.close(), 3000);</script>
        </body></html>
    """)


@router.get("/google/poll")
async def google_poll():
    """Frontend polls this to check if Google auth completed."""
    from services.database import get_setting, set_setting, get_user_by_id
    pending = get_setting("google_pending_auth")
    if not pending:
        return {"authenticated": False}

    # Clear immediately so it can only be consumed once
    set_setting("google_pending_auth", "")

    parts = pending.split("|||")
    if len(parts) != 2:
        return {"authenticated": False}

    token, user_id = parts
    user = get_user_by_id(int(user_id))
    if not user:
        return {"authenticated": False}

    return {"authenticated": True, "token": token, "user": user}


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
