# RecurreTuMulta — Automatización “sin humanos” (pack OPS)

Este pack añade automatización end-to-end **sin intervención humana** salvo que la integración DGT real aún no esté implementada.

## Qué incluye
- `ops_automation.py`: motor idempotente (generate si falta → submit DGT → guarda justificante → status=submitted).
- `dgt_client.py`: interfaz única para integrar DGT homologado.
- Parches para:
  - `ops.py`: endpoints de automatización y descarga.
  - `b2_storage.py`: `download_bytes()` + `presign_get_url()`.

## Endpoints nuevos
- `POST /ops/automation/tick?limit=25`
  - Recorre casos `ready_to_submit` + `paid` + `authorized` y los procesa automáticamente.
- `POST /ops/cases/{case_id}/auto-submit`
  - Procesa un único caso (equivalente a “1 clic”).
- `GET /ops/cases/{case_id}/documents/download?kind=...`
  - Devuelve URL temporal (si puede) o stream del fichero.

> Seguridad: todos protegidos por `X-Operator-Token` igual que el resto de OPS.

## Cron (Render)
Llamar cada 2–5 minutos a:
`POST https://<backend>/ops/automation/tick?limit=25`
Header:
`X-Operator-Token: <OPERATOR_TOKEN>`

## Integración DGT real
Ahora mismo `dgt_client.submit_pdf()` lanza `NotImplementedError` si `DGT_ENABLED` no está configurado.
Cuando tengáis el conector homologado, implementad `submit_pdf()` y ya quedará 100% “sin humanos”.
