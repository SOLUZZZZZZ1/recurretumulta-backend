-- CANDIDATE ONLY. Apply only through the reviewed production migration process.
-- Fiscal identity is stored in RTM and never copied into Stripe metadata.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
SET LOCAL search_path=public,pg_catalog;
DO $invoice_guard$
BEGIN
  IF current_database()<>'recurretumulta'
     OR current_setting('server_version_num')::integer NOT BETWEEN 180000 AND 189999
     OR current_setting('application_name')<>'rtm-production-invoice-migration-reviewed'
  THEN RAISE EXCEPTION 'RTM_INVOICE_MIGRATION_TARGET_REQUIRED'; END IF;
  IF NOT pg_try_advisory_xact_lock(20261108,1)
  THEN RAISE EXCEPTION 'RTM_INVOICE_MIGRATION_BUSY'; END IF;
  IF to_regclass('public.cases') IS NULL OR to_regclass('public.rtm_management_receipts') IS NULL
  THEN RAISE EXCEPTION 'RTM_INVOICE_MIGRATION_PREREQUISITES_MISSING'; END IF;
  IF to_regclass('public.rtm_invoice_requests') IS NOT NULL
  THEN RAISE EXCEPTION 'RTM_INVOICE_REQUESTS_ALREADY_EXIST_REVIEW_REQUIRED'; END IF;
END;
$invoice_guard$;
CREATE TABLE rtm_invoice_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
  stage TEXT NOT NULL CHECK(stage IN ('initial_study','management_balance')),
  reference_id TEXT NOT NULL,
  fiscal_data JSONB NOT NULL CHECK(jsonb_typeof(fiscal_data)='object'),
  recipient_email TEXT NOT NULL CHECK(length(recipient_email) BETWEEN 3 AND 320),
  amount_cents BIGINT CHECK(amount_cents > 0),
  currency TEXT NOT NULL DEFAULT 'eur' CHECK(currency='eur'),
  state TEXT NOT NULL CHECK(state IN ('awaiting_payment','paid_pending_issue','issuing','issued_pending_payment','paid_pending_send','sent','review_required','void')),
  stripe_session_id TEXT,
  payment_intent_id TEXT,
  paid_at TIMESTAMPTZ,
  provider_contact_id TEXT,
  provider_invoice_id TEXT,
  provider_invoice_number TEXT,
  provider_invoice_url TEXT,
  provider_payment_recorded_at TIMESTAMPTZ,
  provider_sent_at TIMESTAMPTZ,
  attempt_started_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
  last_error_code TEXT,
  issued_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(case_id,stage,reference_id),
  CHECK((state NOT IN ('paid_pending_issue','issuing','issued_pending_payment','paid_pending_send','sent') OR
         (stripe_session_id IS NOT NULL AND payment_intent_id IS NOT NULL AND amount_cents > 0 AND paid_at IS NOT NULL)))
);
CREATE INDEX idx_rtm_invoice_requests_pending ON rtm_invoice_requests(state,created_at)
  WHERE state IN ('paid_pending_issue','issuing','issued_pending_payment','paid_pending_send');
COMMIT;
