import unittest
from unittest.mock import patch

from rtm_core.casework_router import management_journey_available
from rtm_core.management_contract import ManagementRejected


READY_FLAGS = {
    "RTM_ENABLE_MANAGEMENT_JOURNEY": "1",
    "RTM_ENABLE_MANAGEMENT_RECONCILIATION": "1",
    "RTM_MANAGEMENT_DRAFT_READY": "1",
    "RTM_MANAGEMENT_GENERATION_WORKER_ENABLED": "1",
    "RTM_MANAGEMENT_MAIL_VERIFIED": "1",
}


class ManagementJourneyCapabilityTests(unittest.TestCase):
    def test_all_required_flags_must_be_enabled_before_policy_validation(self):
        source = {**READY_FLAGS, "RTM_MANAGEMENT_GENERATION_WORKER_ENABLED": "0"}
        with patch("rtm_core.casework_router.management_configuration") as validate:
            self.assertFalse(management_journey_available(source))
        validate.assert_not_called()

    def test_reports_available_only_when_management_policy_is_valid(self):
        with patch("rtm_core.casework_router.management_configuration") as validate:
            self.assertTrue(management_journey_available(READY_FLAGS))
            validate.assert_called_once_with(READY_FLAGS)

    def test_invalid_management_policy_is_reported_as_unavailable(self):
        rejected = ManagementRejected("CONNECTED_DELIVERY_NOT_READY", 503)
        with patch("rtm_core.casework_router.management_configuration", side_effect=rejected):
            self.assertFalse(management_journey_available(READY_FLAGS))


if __name__ == "__main__":
    unittest.main()
