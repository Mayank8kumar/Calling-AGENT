"""
# Authentication & authorization middleware
# Dependencies (used in route functions):
#   get_current_user — extracts JWT from Authorization header, validates, sets tenant RLS
#   get_tenant_id — extracts tenant_id from authenticated user's JWT
#   require_role(*roles) — factory that checks user's role against allowed roles
#   optional_auth — returns user or None (for public endpoints)
# How it works:
#   1. HTTPBearer extracts token from "Authorization: Bearer <token>"
#   2. decode_token() validates JWT signature and expiry
#   3. tenant_id from JWT is set in current_tenant_id ContextVar
#   4. get_db() picks up the ContextVar and sets PostgreSQL RLS context
"""
