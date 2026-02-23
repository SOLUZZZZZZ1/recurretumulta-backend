def _strict_validate_or_raise(conn, case_id: str, core: Dict[str, Any], tpl: Dict[str, str], ai_used: bool) -> None:
    tipo = (core or {}).get("tipo_infraccion") or ""
    body = (tpl or {}).get("cuerpo") or ""
    if (tipo or "").lower() == "velocidad" or _is_velocity_context(core, body):
        missing = _velocity_strict_validate(body)
        if missing:
            raise HTTPException(status_code=422, detail=f"Velocity Strict no cumplido. Faltan/errores: {missing}.")
        # Validación estructural: si hay discrepancia importe/puntos (expected vs impuesto), el cuerpo debe mencionarlo.
        vc = _compute_velocity_calc_from_core(core)
        if isinstance(vc, dict) and vc.get("ok") and vc.get("mismatch"):
            imposed = (vc.get("imposed") or {})
            # Solo exigimos la alegación si el "importe impuesto" es válido (no OCR tipo 120D)
            if isinstance(imposed.get("fine"), int) and "posible error de tramo sancionador" not in (body or "").lower():
                raise HTTPException(
                    status_code=422,
                    detail="Velocity Strict no cumplido. Falta alegación de posible error de tramo sancionador pese a discrepancia detectada.",
                )

# MÓVIL STRICT (SVL-MOV-2)
missing_movil = _movil_strict_validate(body, core)
if missing_movil:
    raise HTTPException(status_code=422, detail=f"Movil Strict no cumplido. Faltan/errores: {missing_movil}.")


# ==========================
# FUNCIÓN PRINCIPAL
# ==========================

def generate_dgt_for_case(
    conn,
    case_id: str,
    interesado: Optional[Dict[str, str]] = None,
    tipo: Optional[str] = None,
) -> Dict[str, Any]:

    row = conn.execute(
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción para ese case_id.")

    extracted_json = row[0]
    wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
    core = wrapper.get("extracted") or {}

    interesado_db = _load_interested_data_from_cases(conn, case_id)
    interesado = _merge_interesado(interesado or {}, interesado_db)

    flags = _load_case_flags(conn, case_id)
    override_mode = bool(flags.get("test_mode")) and bool(flags.get("override_deadlines"))

    if not tipo:
        tipo = "reposicion" if core.get("pone_fin_via_administrativa") is True else "alegaciones"

    tpl: Optional[Dict[str, str]] = None
    ai_used = False
    ai_error: Optional[str] = None

    # auditoría (no rompe si falla)
    decision_mode = "unknown"
    decision: Dict[str, Any] = {"mode": "unknown", "reasons": ["not_computed"]}

    # IA PRIMERO
    if RTM_DGT_GENERATION_MODE != "TEMPLATES_ONLY":
        try:
            ai_result = run_expediente_ai(case_id)
            draft = (ai_result or {}).get("draft") or {}
            asunto = (draft.get("asunto") or "").strip()
            cuerpo = (draft.get("cuerpo") or "").strip()

            if asunto and cuerpo:
                if override_mode:
                    asunto = "RECURSO (MODO PRUEBA)"
                    cuerpo = _strip_borrador_prefix_from_body(cuerpo)

                # Opción B: forzar VSE-1 si es velocidad y el LLM no estructuró bien
                asunto, cuerpo = _force_velocity_vse1_if_needed(asunto, cuerpo, core)

                # Opción determinista SEMÁFORO: si el LLM no estructuró bien, imponemos plantilla fija
                asunto, cuerpo = _force_semaforo_template_if_needed(asunto, cuerpo, core)

                # Opción determinista MÓVIL: si el LLM sale genérico, imponemos plantilla fuerte
                asunto, cuerpo = _force_movil_template_if_needed(asunto, cuerpo, core, capture_mode='UNKNOWN')

                # Decision sobre el cuerpo ya final
                try:
                    decision = decide_modo_velocidad(core, body=cuerpo, capture_mode="UNKNOWN") or decision
                    decision_mode = (decision.get("mode") or "unknown") if isinstance(decision, dict) else "unknown"
                except Exception:
                    pass

                # Bucket paragraph (leve/grave) antes de SOLICITO
                cuerpo = _inject_bucket_paragraph(cuerpo, decision)

                # VSE-1 (velocidad): usamos velocity_calc del engine si está disponible
                velocity_calc = None
                try:
                    vc_engine = (ai_result or {}).get("velocity_calc")
                    if isinstance(vc_engine, dict) and vc_engine.get("ok"):
                        velocity_calc = vc_engine
                except Exception:
                    velocity_calc = None

                if not isinstance(velocity_calc, dict) or not velocity_calc.get("ok"):
                    velocity_calc = _compute_velocity_calc_from_core(core)

                # Párrafo de cálculo ilustrativo (solo si hay datos fiables y aún no está en el cuerpo)
                try:
                    if isinstance(velocity_calc, dict) and velocity_calc.get("ok") and "a efectos ilustrativos" not in (cuerpo or "").lower():
                        calc_p = _build_velocity_calc_paragraph(core)
                        if calc_p:
                            cuerpo = (cuerpo + "\n\n" + calc_p).strip() + "\n"
                except Exception:
                    pass

                # Posible error de tramo (solo si el importe impuesto es válido: ver módulo velocidad)
                cuerpo = _inject_tramo_error_paragraph(cuerpo, core)

                tpl = {"asunto": asunto, "cuerpo": cuerpo}
                ai_used = True
        except Exception as e:
            ai_error = str(e)
            tpl = None

    # FALLBACK A PLANTILLAS
    if not tpl:
        if tipo == "reposicion":
            tpl = build_dgt_reposicion_text(core, interesado)
            filename_base = "recurso_reposicion_dgt"
        else:
            tpl = build_dgt_alegaciones_text(core, interesado)
            filename_base = "alegaciones_dgt"

        # decision también en plantillas (solo auditoría)
        try:
            decision = decide_modo_velocidad(core, body=(tpl.get("cuerpo") or ""), capture_mode="UNKNOWN") or decision
            decision_mode = (decision.get("mode") or decision_mode) if isinstance(decision, dict) else decision_mode
        except Exception:
            pass
    else:
        filename_base = "recurso_reposicion_dgt" if tipo == "reposicion" else "alegaciones_dgt"

    if tipo == "reposicion":
        kind_docx = "generated_docx_reposicion"
        kind_pdf = "generated_pdf_reposicion"
    else:
        kind_docx = "generated_docx_alegaciones"
        kind_pdf = "generated_pdf_alegaciones"

    # Recalcular decision sobre cuerpo definitivo (último punto seguro)
    try:
        if tpl and isinstance(tpl, dict):
            decision = decide_modo_velocidad(core, body=(tpl.get('cuerpo') or ''), capture_mode='UNKNOWN') or decision
            decision_mode = (decision.get('mode') or decision_mode) if isinstance(decision, dict) else decision_mode
    except Exception:
        pass

# FORCE bucket + tramo mismatch injection on final tpl (último punto seguro antes de validar/generar)
    velocity_calc_for_audit: Dict[str, Any] = {"ok": False, "reason": "not_computed"}
    try:
        if tpl and isinstance(tpl, dict):
            tpl["cuerpo"] = _inject_bucket_paragraph(tpl.get("cuerpo") or "", decision)
            velocity_calc_for_audit = _compute_velocity_calc_from_core(core)
            tpl["cuerpo"] = _inject_tramo_error_paragraph(tpl.get("cuerpo") or "", core)
    except Exception:
        pass

    # STRICT
    _strict_validate_or_raise(conn, case_id, core, tpl, ai_used)

    # DOCX/PDF
    docx_bytes = build_docx(tpl["asunto"], tpl["cuerpo"])
    b2_bucket, b2_key_docx = upload_bytes(
        case_id,
        "generated",
        docx_bytes,
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    pdf_bytes = build_pdf(tpl["asunto"], tpl["cuerpo"])
    _, b2_key_pdf = upload_bytes(
        case_id,
        "generated",
        pdf_bytes,
        ".pdf",
        "application/pdf",
    )

    conn.execute(
        text("INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id,:kind,:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"),
        {
            "case_id": case_id,
            "kind": kind_docx,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_docx,
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(docx_bytes),
        },
    )
    conn.execute(
        text("INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id,:kind,:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"),
        {
            "case_id": case_id,
            "kind": kind_pdf,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_pdf,
            "mime": "application/pdf",
            "size_bytes": len(pdf_bytes),
        },
    )

    conn.execute(
        text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:case_id,'resource_generated',CAST(:payload AS JSONB),NOW())"),
        {
            "case_id": case_id,
            "payload": json.dumps(
                {
                    "tipo": tipo,
                    "ai_used": ai_used,
                    "ai_error": ai_error,
                    "generation_mode": RTM_DGT_GENERATION_MODE,
                    "override_mode": override_mode,
                    "missing_interested_fields": _missing_interested_fields(interesado),
                    "velocity_decision_mode": decision_mode,
                    "velocity_decision": decision,
                    "velocity_calc": velocity_calc_for_audit,
                }
            ),
        },
    )

    conn.execute(text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:case_id"), {"case_id": case_id})

    return {
        "ok": True,
        "case_id": case_id,
        "tipo": tipo,
        "filename_base": filename_base,
        "ai_used": ai_used,
        "ai_error": ai_error,
        "override_mode": override_mode,
        "velocity_decision_mode": decision_mode,
    }


# ==========================
# ENDPOINT
# ==========================

class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)
    tipo: Optional[str] = None


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado, tipo=req.tipo)
    return {"ok": True, "message": "Recurso generado en DOCX y PDF.", **result}
