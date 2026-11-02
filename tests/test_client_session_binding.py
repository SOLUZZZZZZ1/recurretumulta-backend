import unittest
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4
from rtm_core.management_router import AcceptanceInput,CheckoutInput,accept,checkout
from rtm_core.management_contract import ManagementRejected

class SessionBindingTest(unittest.TestCase):
    def test_cookie_session_change_denied_before_acceptance_or_checkout_reservation(self):
        values={'quote_id':str(uuid4()),'quote_sha256':'a'*64,'expected_session_ref':'b'*64}
        grant={'session_ref':'c'*64,'recipient_hash':'d'*64}
        for endpoint,body,target in [(accept,AcceptanceInput(**values,operation_id=str(uuid4()),accept_exact_version=True),'accept_quote'),(checkout,CheckoutInput(**values),'reserve')]:
            with self.subTest(endpoint=endpoint.__name__),patch('rtm_core.management_router.'+target) as mutation:
                with self.assertRaises(ManagementRejected) as failure:endpoint(body,(None,grant,None,str(uuid4())))
                self.assertEqual(failure.exception.status,403);mutation.assert_not_called()
