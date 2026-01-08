# RecurreTuMulta Backend (mínimo con Postgres)

## Variables de entorno (Render)
- DATABASE_URL = (Internal Database URL de Render Postgres)
- ADMIN_TOKEN = token largo secreto (para /admin/migrate/init)
- ALLOWED_ORIGINS = https://recurretumulta.eu,https://www.recurretumulta.eu (opcional)

## Start Command (Render)
uvicorn app:app --host 0.0.0.0 --port $PORT

## Health
GET /health

## Migración inicial (crear tablas)
POST /admin/migrate/init
Header: x-admin-token: <ADMIN_TOKEN>

Ejemplo (curl):
curl -X POST https://recurretumulta-backend.onrender.com/admin/migrate/init -H "x-admin-token: TU_TOKEN"
