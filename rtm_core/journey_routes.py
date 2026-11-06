"""Recognize only mounted journey endpoints with their own individual guards.

Do not exempt an entire CORE prefix: historical CORE routes still require the
published bridge. A full route/method match avoids broad prefix exemptions.
"""
from starlette.routing import Match


def is_individually_guarded_journey(scope):
    if scope.get('type') != 'http' or not scope.get('path', '').startswith('/ops/core/cases'):
        return False
    from rtm_core.casework_router import router as casework
    from rtm_core.case_mail_router import router as mail
    from rtm_core.management_router import router as management
    return any(route.matches(scope)[0] is Match.FULL
               for router in (casework, mail, management) for route in router.routes)
