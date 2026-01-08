from pydantic import BaseModel, Field
from typing import Any, Optional, Dict, List

class HealthResponse(BaseModel):
    ok: bool = True

class MigrateResponse(BaseModel):
    ok: bool
    message: str
    created: List[str] = Field(default_factory=list)

class AnalyzeResponse(BaseModel):
    ok: bool
    message: str
    case_id: Optional[str] = None
    extracted: Optional[Dict[str, Any]] = None
