# Cobro final de recurso de multa: continuidad

## Decision confirmada por Ramon

El 18 de septiembre de 2026, Ramon confirmo un total de 39 EUR para el
recurso administrativo de multa. Los 10 EUR de revision inicial se descuentan
cuando estan pagados. Confirmo expresamente que el saldo correcto es 29 EUR,
tras corregir una cifra anterior. Se trata de un unico total, no de 39 EUR
adicionales a los 10 EUR.

La pagina de precios muestra esta tarifa y el ejemplo del saldo. La tarifa
publica no acredita pago, presupuesto aceptado, autorizacion ni capacidad de
ejecutar una actuacion. No reutilizar el producto de eliminacion de vehiculo.

## Estado real del codigo

`billing.py:create_checkout` rechaza la fase final legacy con 409 hasta que
exista un presupuesto aprobado, persistido, versionado y ligado al expediente.
No quitar ese rechazo para crear un enlace manual. El flujo de revision
actual tiene intenciones duraderas, clave de idempotencia y conciliacion de
webhooks que deben conservarse al implementar el nuevo flujo final.

No se ha creado producto, Price, Checkout Session o Payment Link en Stripe.
No hay acceso a la cuenta Stripe desde el entorno de preparacion.
Los pagos permanecen desactivados en el perfil local.

## Trabajo siguiente concreto

1. Crear el presupuesto del expediente `traffic/fine` desde el catalogo de
   multa: total 3900 centimos EUR, con su version y aprobacion persistidas.
2. Consultar evidencia conciliada del primer pago del mismo expediente;
   acreditar los 1000 centimos una sola vez. Revisar devoluciones y disputas.
   Un estado enviado por el navegador no es evidencia de pago.
3. Guardar total, credito, saldo y referencias de evidencia. Para el caso
   confirmado por Ramon: 3900 - 1000 = 2900 centimos EUR.
4. Crear el precio Stripe de pago unico de 29 EUR para el saldo, si se usa
   un Price fijo, y validar su moneda e importe desde el servidor. Preparar
   y probar primero en modo test, con identificadores distintos de live.
5. Crear la Checkout Session para ese expediente y presupuesto; asociar
   `client_reference_id` y metadata internas. Devolver su URL al expediente.
   Reutilizar la intencion idempotente para evitar sesiones o cobros duplicados.
6. Conciliar mediante webhook verificado y pago efectivamente liquidado.
   Un retorno a la pagina de exito no acredita por si solo el cobro final.

Documentacion oficial consultada:
[Crear una Checkout Session](https://docs.stripe.com/api/checkout/sessions/create).
Este documento es un relevo de implementacion, no un cobro ya habilitado.
