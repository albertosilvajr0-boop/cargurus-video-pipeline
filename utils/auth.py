"""Firebase Authentication middleware for multi-tenant support.

Provides optional authentication that can be enabled via environment variable.
When AUTH_ENABLED=true, all API routes require a valid Firebase ID token.
When disabled, the app works as before (no auth required).

Multi-tenancy: Each authenticated user gets their own data namespace (tenant_id).
"""

import functools
import os

from flask import request, jsonify, g

from utils.logger import get_logger

logger = get_logger("auth")

# Auth configuration
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "false").lower() == "true"
AUTH_PROVIDER = os.environ.get("AUTH_PROVIDER", "firebase")  # firebase or api_key

# Simple API key auth (for programmatic access / DMS integrations)
API_KEYS = set(filter(None, os.environ.get("API_KEYS", "").split(",")))

# Firebase Admin SDK (lazy-initialized)
_firebase_app = None


def _get_firebase_app():
    """Initialize Firebase Admin SDK for token verification."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    try:
        import firebase_admin
        from firebase_admin import credentials

        cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "")
        if cred_path:
            cred = credentials.Certificate(cred_path)
            _firebase_app = firebase_admin.initialize_app(cred)
        else:
            # Use Application Default Credentials (works on Cloud Run)
            _firebase_app = firebase_admin.initialize_app()
        logger.info("Firebase Admin SDK initialized for auth")
        return _firebase_app
    except Exception as e:
        logger.warning("Firebase Admin SDK not available: %s", e)
        return None


def _verify_firebase_token(token: str) -> dict | None:
    """Verify a Firebase ID token and return the decoded claims."""
    try:
        from firebase_admin import auth
        _get_firebase_app()
        decoded = auth.verify_id_token(token)
        return decoded
    except Exception as e:
        logger.debug("Token verification failed: %s", e)
        return None


def _extract_token() -> str | None:
    """Extract auth token from request headers."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    # Also check API key header
    return request.headers.get("X-API-Key")


def get_current_user() -> dict | None:
    """Get the current authenticated user from Flask's g context."""
    return getattr(g, "current_user", None)


def get_tenant_id() -> str:
    """Get the current tenant ID.

    Returns 'default' when auth is disabled (single-tenant mode).
    Returns the user's UID when auth is enabled (multi-tenant mode).
    """
    user = get_current_user()
    if user:
        return user.get("uid", "default")
    return "default"


def require_auth(f):
    """Decorator to require authentication on a route.

    When AUTH_ENABLED is False, this is a no-op (passes through).
    When AUTH_ENABLED is True, validates the token and sets g.current_user.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not AUTH_ENABLED:
            g.current_user = None
            return f(*args, **kwargs)

        token = _extract_token()
        if not token:
            return jsonify({"error": "Authentication required", "code": "auth_required"}), 401

        # Check API key first (for programmatic access)
        if token in API_KEYS:
            g.current_user = {"uid": "api_key", "provider": "api_key"}
            return f(*args, **kwargs)

        # Verify Firebase token
        if AUTH_PROVIDER == "firebase":
            claims = _verify_firebase_token(token)
            if not claims:
                return jsonify({"error": "Invalid or expired token", "code": "invalid_token"}), 401
            g.current_user = {
                "uid": claims["uid"],
                "email": claims.get("email"),
                "name": claims.get("name"),
                "provider": "firebase",
            }
            return f(*args, **kwargs)

        return jsonify({"error": "Authentication failed"}), 401

    return decorated


def optional_auth(f):
    """Decorator that attempts auth but doesn't require it.

    Sets g.current_user if a valid token is present, but allows
    unauthenticated access. Useful for public endpoints that
    behave differently when authenticated.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        g.current_user = None
        token = _extract_token()
        if token:
            if token in API_KEYS:
                g.current_user = {"uid": "api_key", "provider": "api_key"}
            elif AUTH_PROVIDER == "firebase":
                claims = _verify_firebase_token(token)
                if claims:
                    g.current_user = {
                        "uid": claims["uid"],
                        "email": claims.get("email"),
                        "name": claims.get("name"),
                        "provider": "firebase",
                    }
        return f(*args, **kwargs)

    return decorated


# --- Auth status endpoint ---

def register_auth_routes(app):
    """Register auth-related routes on the Flask app."""

    @app.route("/api/auth/status")
    def auth_status():
        """Check authentication status and configuration."""
        return jsonify({
            "auth_enabled": AUTH_ENABLED,
            "auth_provider": AUTH_PROVIDER if AUTH_ENABLED else None,
            "api_key_configured": len(API_KEYS) > 0,
        })

    @app.route("/api/auth/me")
    @require_auth
    def auth_me():
        """Get the current authenticated user's info."""
        user = get_current_user()
        if user:
            return jsonify(user)
        return jsonify({"user": None})
