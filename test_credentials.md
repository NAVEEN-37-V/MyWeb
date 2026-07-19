"# Test Credentials

## Admin Account
- Email: `admin@4starabove.com`
- Password: `Admin@12345`
- Role: admin

## Auth Endpoints
- POST `/api/auth/login` (email, password) — returns user, sets httpOnly cookies
- POST `/api/auth/logout` (authenticated)
- GET `/api/auth/me` (authenticated)

## Notes
- Cookies: `access_token`, `refresh_token` (httpOnly)
- The frontend axios calls MUST use `withCredentials: true`.
"
