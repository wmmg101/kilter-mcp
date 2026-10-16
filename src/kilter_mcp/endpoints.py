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

# Climb catalogue (authenticated). Both return ``{"climbs": [...], "climbStats": [...]}``.
# ``logged`` is the user's own logged climbs with their edge boxes (tells us what fits on the
# wall); ``curated`` is Kilter's featured list (~6k climbs, ~5 MB), the only catalogue read that
# is small enough to fetch on demand. The per-layout ``/api/climbs/all/{layout}`` dumps run to
# hundreds of MB and are scoped to a climb's own layout, not the wall's, so they are not used.
LOGGED_CLIMBS_URL = f"{PORTAL_BASE}/api/climbs/logged"
CURATED_CLIMBS_URL = f"{PORTAL_BASE}/api/climbs/curated"


def climb_stats_url(climb_uuid: str) -> str:
    """Community stats for one climb at every angle (~3 KB). Used to grade a climb the user
    has sent at one angle when planning to repeat it at another."""
    return f"{PORTAL_BASE}/api/climb-stat/{climb_uuid}"


# Redacted in error messages so headers/tokens never leak.
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie"})
