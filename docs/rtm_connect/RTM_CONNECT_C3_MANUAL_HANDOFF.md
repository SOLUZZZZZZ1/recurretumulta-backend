# RTM CONNECT C3 · manual_handoff normalizado

## Objetivo

C3 convierte una presentación manual en un conector RTM trazable. No automatiza
ninguna sede: prepara un paquete congelado, crea una tarea, asigna operador,
controla una fecha límite, captura un justificante sintético, verifica su hash y
referencia y solo entonces permite que CORE confirme la actuación.

## Cadena

```text
CORE autoriza
→ CONNECT crea acción e intento manual
→ paquete documental congelado
→ tarea asignada
→ operador ejecuta fuera de RTM
→ justificante capturado como E3
→ verificador distinto comprueba hash y referencia
→ evidencia E4
→ CORE confirma
```

## Persistencia

- `rtm_connect_manual_tasks`: estado, asignación, plazo y paquete.
- `rtm_connect_manual_events`: historial append-only.

El paquete, `due_at`, instrucciones, acción, intento y conector quedan
congelados. La asignación queda congelada tras pasar a `assigned`.

## Estados

```text
prepared → assigned → in_progress → awaiting_receipt
→ receipt_submitted → verified → completed
```

## Separación de funciones

El operador asignado puede ejecutar el paso manual y aportar el justificante,
pero no puede verificarlo. La verificación exige otro operador y genera E4.

## Alcance de staging

C3 utiliza únicamente almacenamiento `synthetic://manual-handoff/`. Funciona
sin rutas, sin red, sin B2, sin correo, pagos, Stripe, sedes ni presentación
externa real. El conector se registra solo dentro del smoke y desaparece por
rollback.

## Criterio de cierre

Migración aditiva; preflight `safe=true`; smoke completo; cero acciones,
conectores, tareas, eventos, operadores y roles sintéticos tras rollback;
`/health` correcto; restore remoto verificado.
