from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from database import get_engine

router = APIRouter(prefix="/ops", tags=["ops-override"])

@router.post("/cases/{case_id}/force-generate")
def force_generate_resource(case_id: str):
    """
    Forzar generación de recurso SOLO PARA PRUEBA.
    - Ignora plazos
    - NO permite presentación
    - Marca el expediente como TEST
    """
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM cases WHERE id = :id"),
            {"id": case_id}
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Expediente no encontrado")

        conn.execute(
            text("""
                UPDATE cases
                SET
                  test_mode = true,
                  override_deadlines = true,
                  updated_at = NOW()
                WHERE id = :id
            """),
            {"id": case_id}
        )

    return {
        "ok": True,
        "mode": "TEST_ONLY",
        "message": "Expediente marcado para generación de recurso en modo prueba"
    }
