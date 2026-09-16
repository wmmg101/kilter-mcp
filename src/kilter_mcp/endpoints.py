"""All Kilter URLs in one place.

These endpoints are used by the official Kilter apps but are not a documented public API.
They may change without notice; keep every URL here so updates are a one-file change.
"""

from __future__ import annotations

IDP_BASE = "https://idp.kiltergrips.com"
PORTAL_BASE = "https://portal.kiltergrips.com"

# Keycloak realm "kilter", public client "kilter".
TOKEN_URL = f"{IDP_BASE}/realms/kilter/protocol/openid-connect/token"
CLIENT_ID = "kilter"
SCOPE = "openid offline_access"

# Authenticated user's logbook (bearer token identifies the user).
LOGS_URL = f"{PORTAL_BASE}/api/logs"

# Difficulty table (served without authentication).
GRADES_URL = f"{PORTAL_BASE}/api/grades"

# Redacted in error messages so headers/tokens never leak.
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie"})
