# RTM — Libro de reservas (restaurante)

## Variables de entorno (backend)
- RESERVAS_REST_PIN=7391   # Cambia a tu gusto (obligatorio)
- ADMIN_TOKEN=...          # Ya lo usas para /admin/migrate/* (obligatorio para migrar)

## Migración (una vez)
POST /admin/migrate/restaurant_reservations
Header: x-admin-token: <ADMIN_TOKEN>

## Endpoints (uso)
Todos requieren header:
- x-reservas-pin: <PIN>

Listar:
GET /ops/restaurant-reservations?date=YYYY-MM-DD&shift=comida

Crear:
POST /ops/restaurant-reservations
JSON: { reservation_date, reservation_time, shift, table_name, party_size, customer_name, phone, extras_dog, extras_celiac, extras_notes, created_by }

Acciones rápidas:
POST /ops/restaurant-reservations/{id}/arrived
POST /ops/restaurant-reservations/{id}/no-show

## Frontend
Ruta oculta:
- /__reservas-restaurante
Pide PIN y lo guarda en sessionStorage.
