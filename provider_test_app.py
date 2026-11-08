"""Entrypoint exclusively for the separate provider TEST service.

Validate before importing the legacy application, including its initial billing
routes. No new endpoint or startup migration is introduced.
"""
import os
from rtm_core.casework_policy import validate_test_boundary, CaseworkConfigurationError

validate_test_boundary(os.environ)
from app import app
from fastapi.responses import JSONResponse


@app.middleware('http')
async def require_provider_test_boundary(request, call_next):
    try:validate_test_boundary(os.environ)
    except CaseworkConfigurationError:
        return JSONResponse({'detail':'TEST deployment configuration unavailable'},status_code=503,headers={'Cache-Control':'no-store'})
    if request.url.path != '/health' and os.getenv('RTM_PROVIDER_TEST_HTTP_ENABLED') != '1':
        return JSONResponse({'detail':'TEST journey not open yet'},status_code=503,headers={'Cache-Control':'no-store'})
    return await call_next(request)
