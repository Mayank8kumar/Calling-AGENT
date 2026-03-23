"""
# Authentication routes
# POST /auth/register — creates tenant + admin user, returns JWT tokens
# POST /auth/login — validates email/password, returns access + refresh tokens
# POST /auth/refresh — exchanges refresh token for new access token
# GET  /auth/me — returns current user info (requires auth)
"""
