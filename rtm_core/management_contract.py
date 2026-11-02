"""Server-only economics and immutable offers, adapted from October pilot_quotes.

No provider calls, persistence, permission grants or payment-status mutations.
TEST receipts never satisfy a LIVE offer. A commercial expiry is not a deadline.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT = 'rtm_management_quote_v1'
MIN_TOTAL_CENTS = 3900
INITIAL_STUDY_CENTS = 1000


class ManagementRejected(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ManagementRejected('COMMERCIAL_EXPIRY_INVALID', 422) from None


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation_id: str
    expected_quote_id: str | None
    study_operation_id: str
    study_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    review_confirmed: Literal[True]
    title: str = Field(min_length=5, max_length=200)
    scope: str = Field(min_length=20, max_length=12000)
    exclusions: str = Field(min_length=5, max_length=4000)
    client_summary: str = Field(min_length=20, max_length=12000)
    total_cents: int = Field(ge=3900, le=1000000)
    expires_at: str

    @field_validator('operation_id', 'expected_quote_id', 'study_operation_id')
    @classmethod
    def identifier(cls, value):
        if value is not None and str(UUID(value)) != value:
            raise ValueError('Canonical operation reference required')
        return value

    @field_validator('title', 'scope', 'exclusions', 'client_summary')
    @classmethod
    def nonempty(cls, value):
        if not value.strip() or any(ord(c) < 32 and c not in '\n\t\r' for c in value):
            raise ValueError('Plain nonempty text required')
        return value


def amounts(total_cents, receipt, *, mode, case_id, account_id, vat_basis_points, tax_label,
            agreed_total_cents=None):
    if mode not in {'test', 'live'}:
        raise ManagementRejected('PAYMENT_MODE_UNAVAILABLE', 503)
    if (type(total_cents) is not int or not MIN_TOTAL_CENTS <= total_cents <= 1000000
            or (agreed_total_cents is not None and total_cents != agreed_total_cents)):
        raise ManagementRejected('AGREED_TOTAL_MISMATCH', 422)
    if (type(vat_basis_points) is not int or not 0 <= vat_basis_points <= 10000
            or not isinstance(tax_label, str) or not tax_label.strip()):
        raise ManagementRejected('TAX_CONFIGURATION_UNVERIFIED', 503)
    # Only a provider-verified receipt admitted by the server may reach here.
    if not isinstance(receipt, dict):
        raise ManagementRejected('STUDY_RECEIPT_NOT_VERIFIED')
    proof = {k: v for k, v in receipt.items() if k != 'evidence_sha256'}
    if (receipt.get('case_id') != case_id or receipt.get('stage') != 'initial_study'
            or receipt.get('mode') != mode or receipt.get('account_id') != account_id
            or receipt.get('currency') != 'eur' or receipt.get('amount_cents') != INITIAL_STUDY_CENTS
            or type(receipt.get('amount_cents')) is not int
            or receipt.get('provider_status') != 'succeeded'
            or receipt.get('refunded_cents') != 0 or receipt.get('disputed') is not False
            or receipt.get('evidence_sha256') != digest(proof)):
        raise ManagementRejected('STUDY_RECEIPT_NOT_VERIFIED')
    net = int((Decimal(total_cents) * 10000 / (10000 + vat_basis_points)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return {'currency': 'eur', 'total_cents': total_cents, 'net_cents': net,
            'tax_cents': total_cents - net, 'tax_basis_points': vat_basis_points,
            'tax_label': tax_label, 'tax_included': True,
            'study_credit_cents': INITIAL_STUDY_CENTS,
            'balance_cents': total_cents - INITIAL_STUDY_CENTS}


def study_requirements(study):
    if not study:
        return ['STUDY_NOT_SAVED']
    snapshot = study.get('snapshot', {})
    if study.get('sha256') != digest(snapshot):
        raise ManagementRejected('STUDY_INTEGRITY_UNVERIFIED', 503)
    content = snapshot.get('study', {})
    missing = []
    if content.get('state') != 'completed':
        missing.append('STUDY_NOT_COMPLETED')
    for key in ('organismo', 'expediente_ref', 'notified_at', 'facts', 'deadline'):
        fact = content.get('facts', {}).get(key, {})
        if fact.get('status') != 'validated' or not fact.get('value') or not fact.get('sources'):
            missing.append('REVIEW_REQUIRED_' + key.upper())
    if not content.get('assessment', '').strip():
        missing.append('ASSESSMENT_REQUIRED')
    if content.get('missing', '').strip():
        missing.append('STUDY_HAS_UNRESOLVED_REQUIREMENTS')
    kinds = {item.get('kind') for item in snapshot.get('sources', [])
             if item.get('read_confirmed_by_operator') is True}
    if not {'original', 'authorization_signed'}.issubset(kinds) or not kinds.intersection({'identity_front', 'identity_back'}):
        missing.append('REQUIRED_DOCUMENTS_NOT_REVIEWED')
    return missing


def offer(body, *, case_id, author, study, receipt, mode, account_id, revision,
          tax_basis_points, tax_label, now=None, agreed_total_cents=None):
    now = now or datetime.now(timezone.utc)
    missing = study_requirements(study)
    if missing:
        raise ManagementRejected(missing[0])
    snapshot = study['snapshot']
    if (snapshot.get('case_id') != case_id or snapshot.get('operation_id') != body.study_operation_id
            or study['sha256'] != body.study_sha256):
        raise ManagementRejected('QUOTE_STUDY_VERSION_CHANGED')
    expiry = timestamp(body.expires_at)
    if not now < expiry <= now + timedelta(days=90):
        raise ManagementRejected('COMMERCIAL_EXPIRY_INVALID', 422)
    economics = amounts(body.total_cents, receipt, mode=mode, case_id=case_id, account_id=account_id,
                        vat_basis_points=tax_basis_points, tax_label=tax_label, agreed_total_cents=agreed_total_cents)
    # Author comes exclusively from the authenticated server session.
    material = {'contract': CONTRACT, 'id': body.operation_id, 'case_id': case_id,
        'revision': revision, 'previous_quote_id': body.expected_quote_id,
        'author': {'id': author.operator_id, 'name': author.display_name},
        'created_at': now.isoformat(), 'expires_at': expiry.isoformat(),
        'expiry_meaning': 'commercial_offer_only', 'study_operation_id': body.study_operation_id,
        'study_sha256': body.study_sha256, 'review_confirmed_by': author.operator_id,
        'title': body.title, 'scope': body.scope, 'exclusions': body.exclusions,
        'client_summary': body.client_summary, 'economics': economics, 'mode': mode,
        'study_receipt_id': receipt['id'], 'study_receipt_sha256': receipt['evidence_sha256'],
        'submission_authorized': False}
    return {'snapshot': material, 'sha256': digest(material), 'canonical_snapshot': canonical(material)}


def verify_quote(value, *, case_id):
    snapshot = value.get('snapshot', {})
    if (snapshot.get('contract') != CONTRACT or snapshot.get('case_id') != case_id
            or snapshot.get('submission_authorized') is not False
            or value.get('sha256') != digest(snapshot)):
        raise ManagementRejected('QUOTE_INTEGRITY_UNVERIFIED', 503)
    return value
