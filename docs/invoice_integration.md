# Factura requests and FacturaDirecta

The client can request an invoice at either paid stage. RTM keeps the fiscal
details in `rtm_invoice_requests`; they are never copied to Stripe metadata.
The queue starts only when `RTM_INVOICE_WORKER_ENABLED=1`. It waits for the
verified Stripe payment, creates a final invoice, records the same gross amount
as received into the configured clearing bank account, and asks FacturaDirecta
to email the invoice PDF to the verified case email.

## Required production preparation

1. Configure the FacturaDirecta company issuer profile with the correct legal
   entity, NIF, address, invoice numbering series, VeriFactu status and SMTP
   sender. Confirm all of these in the provider account before issuing any
   final invoice.
2. Configure the sales tax catalog's current Spanish standard VAT at 21%, and
   obtain its exact tax ID. RTM checks that the configured ID is current,
   Spanish, standard VAT, and exactly 21% before it issues an invoice.
3. Create a clearing bank account in FacturaDirecta for Stripe settlements and
   obtain its `ban_*` ID. Obtain the income ledger account used for these RTM
   services and the invoice series identifier from the provider settings.
4. Create a dedicated API key with only the required access: contacts read/write,
   invoices write, and settings read. Store it in Render as
   `RTM_INVOICE_FD_API_KEY`; never paste it into chat or source control.
5. Apply `20261108_invoice_requests_candidate.sql` only after confirming its
   database guard and production migration procedure. Deploy the backend and
   frontend changes before enabling the queue.
6. Set `RTM_INVOICE_WORKER_ENABLED=1`, then set the six `RTM_INVOICE_FD_*`
   values used by the worker. Set `RTM_INVOICE_FD_VAT_BPS=2100` and set the
   `RTM_INVOICE_FD_ISSUER_CONFIRMED`, `RTM_INVOICE_FD_VERIFACTU_CONFIRMED`,
   and `RTM_INVOICE_FD_MAIL_CONFIRMED` flags to `1` only after those settings
   have been checked in FacturaDirecta.

Until all required settings are present, the worker fails closed and sends
nothing. The worker routes non-Spanish and Canary Islands, Ceuta or Melilla
fiscal addresses to OPS review rather than applying 21% automatically. Any
provider validation error also goes to review; it never logs invoice payloads,
API keys, or provider response bodies. OPS accounting status is available at
`GET /ops/automation/invoices` with the existing `X-Operator-Token`.

Prices are treated as tax-inclusive gross amounts: the invoice total matches
the amount Stripe actually collected. The provider calculates the base and VAT
backwards from that total. No extra tax is charged to the customer.
