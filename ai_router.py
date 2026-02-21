from fastapi import HTTPException

@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        result = run_expediente_ai(req.case_id)

        engine = get_engine()
        always_generate = (os.getenv("ALWAYS_GENERATE_ON_AI_RUN") or "").strip() == "1"

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false) "
                    "FROM cases WHERE id=:id"
                ),
                {"id": req.case_id},
            ).fetchone()

            test_mode = bool(row[0]) if row else False
            override_deadlines = bool(row[1]) if row else False

            if always_generate or (test_mode and override_deadlines):
                generate_dgt_for_case(conn, req.case_id)
                conn.execute(
                    text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:id"),
                    {"id": req.case_id},
                )
                result["note"] = "Modo Dios: recurso generado para revisión (sin presentar)"

        return result

    except HTTPException:
        # 🔥 MUY IMPORTANTE: no transformar 422 en 500
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error Modo Dios (inesperado): {e}")