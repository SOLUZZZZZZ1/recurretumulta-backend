"""RTM — SEMÁFORO (SVL-SEM-4) — DEMOLEDOR 9.5/10 (Enfoque operativo)

Modo B por defecto (maximiza archivo real).
Modo C solo graves (puntos/sanción alta).
Compatibilidad: build_semaforo_strong_template(core)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime
import re


def _get_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(round(v))
        s = str(v).strip()
        if not s:
            return None
        s = s.replace("€", "").replace(".", "").replace(",", "").strip()
        return int(s) if s.isdigit() else None
    except Exception:
        return None


def _is_grave(core: Dict[str, Any]) -> bool:
    core = core or {}
    fine = _get_int(core.get("sancion_importe_eur") or core.get("importe") or core.get("importe_total_multa"))
    pts = _get_int(core.get("puntos_detraccion") or core.get("puntos") or 0) or 0
    if pts and pts > 0:
        return True
    if fine is not None and fine >= 500:
        return True
    g = str(core.get("gravedad") or "").lower().strip()
    return g in ("grave", "muy grave", "critico", "crítico")


def build_semaforo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    modo_c = _is_grave(core)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "NO RESPETAR LA LUZ ROJA (SEMÁFORO)."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — ELEMENTO OBJETIVO: FASE ROJA ACTIVA Y REBASE EFECTIVO\n\n"
        "Para sancionar por no respetar la luz roja no intermitente debe acreditarse de forma objetiva y verificable:\n"
        "1) Que existía FASE ROJA ACTIVA en el instante exacto del supuesto rebase.\n"
        "2) Que el vehículo rebasó efectivamente la LÍNEA DE DETENCIÓN con la fase roja ya activa.\n"
        "3) Que no se trataba de fase ámbar o transición del ciclo semafórico.\n"
        "4) Identificación inequívoca del vehículo y correspondencia temporal exacta del registro.\n\n"
        "No consta acreditación suficiente de dichos extremos con soporte verificable, por lo que no puede tenerse por probado el hecho infractor.\n\n"
        "ALEGACIÓN SEGUNDA — SECUENCIA ÍNTEGRA, SIN RECORTES, Y SINCRONIZACIÓN HORARIA\n\n"
        "En captación automática, no basta un fotograma aislado o recortado. Se requiere secuencia completa (mínimo dos/tres imágenes o vídeo) "
        "que permita verificar fase roja efectiva, posición del vehículo respecto de la línea de detención y cronometría.\n\n"
        "Debe aportarse también documentación técnica del sistema (homologación/certificación del dispositivo y del conjunto semáforo-captación), "
        "y acreditación de sincronización horaria y correcto funcionamiento en la fecha del hecho.\n\n"
        "En observación por agente, debe detallarse posición, distancia, ángulo, visibilidad y circunstancias que permitan verificar que el rebase se produjo con fase roja activa (no ámbar).\n\n"
        "ALEGACIÓN TERCERA — MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "La resolución debe contener motivación individualizada, evitando fórmulas estereotipadas, identificando instante exacto, ciclo del semáforo, rebase de la línea de detención y soporte probatorio aportado.\n"
    )

    if modo_c:
        cuerpo += (
            "\nALEGACIÓN ADICIONAL (MODO C — GRAVEDAD): EXIGENCIA REFORZADA DE PRUEBA\n\n"
            "Cuando la sanción incorpora pérdida de puntos o especial gravedad, la exigencia de prueba verificable y motivación es máxima. "
            "En ausencia de secuencia íntegra, sincronización y acreditación técnica del sistema, procede el archivo y, en su caso, la anulación por falta de motivación suficiente.\n"
        )

    cuerpo += (
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa (secuencia íntegra sin recortes, homologación/certificación, sincronización horaria y motivación detallada).\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "fase roja" not in b and "roja activa" not in b:
        missing.append("fase_roja")
    if "secuencia" not in b:
        missing.append("secuencia")
    if "sincron" not in b:
        missing.append("sincronizacion")
    if "línea de detención" not in b and "linea de detencion" not in b:
        missing.append("linea_detencion")
    if "archivo" not in b:
        missing.append("archivo")
    out=[]
    seen=set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# =============================================================================
# RTM Intelligence CORE — SEMÁFORO
# =============================================================================

SEMAFORO_LEGAL_INTELLIGENCE_VERSION = "semaforo_legal_v1_0"


def _s_safe(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _s_fold(v: Any) -> str:
    import unicodedata
    txt = unicodedata.normalize("NFKD", _s_safe(v))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"\s+", " ", txt).lower().strip()
    return txt


def _s_date(value: Any) -> Optional[datetime]:
    txt = _s_safe(value).strip().replace("/", "-").replace(".", "-")
    m = re.search(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b", txt)
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except Exception:
        return None


def _s_secondary(core: Dict[str, Any]) -> Dict[str, Any]:
    sec = (core or {}).get("semaforo_secondary_facts")
    return dict(sec) if isinstance(sec, dict) else {}


def _s_secondary_meta(core: Dict[str, Any]) -> Dict[str, Any]:
    raw_conf = (
        dict((core or {}).get("semaforo_secondary_facts_confidence") or {})
        if isinstance((core or {}).get("semaforo_secondary_facts_confidence"), dict)
        else {}
    )
    # La comprobación genérica de identidad de Generate espera una confianza
    # agregada en "document_subject".
    try:
        name_conf = float(raw_conf.get("document_subject_name") or 0)
    except Exception:
        name_conf = 0.0
    try:
        id_conf = float(raw_conf.get("document_subject_id") or 0)
    except Exception:
        id_conf = 0.0
    raw_conf["document_subject"] = max(name_conf, id_conf)

    return {
        "version": _s_safe((core or {}).get("semaforo_secondary_facts_version")).strip() or None,
        "confidence": raw_conf,
        "evidence": (
            dict((core or {}).get("semaforo_secondary_facts_evidence") or {})
            if isinstance((core or {}).get("semaforo_secondary_facts_evidence"), dict)
            else {}
        ),
    }


def _s_document_precept_analysis(core: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    ref = secondary.get("normative_reference")
    ref = dict(ref) if isinstance(ref, dict) else {}
    norm = _s_safe(ref.get("norm")).strip() or None
    article = _s_safe(ref.get("article")).strip() or None
    fact_date = _s_date((core or {}).get("fecha_infraccion"))

    result = {
        "document_norm": norm,
        "document_article": article,
        "status": "unknown",
        "requires_review": False,
        "legal_rule_at_fact_date": None,
        "article_143_role_at_fact_date": None,
        "automatic_invalidity": False,
    }

    if fact_date and fact_date >= datetime(2025, 7, 1):
        result["legal_rule_at_fact_date"] = "RGC Anexo I, apartado 4.2.a — luz roja no intermitente"
        result["article_143_role_at_fact_date"] = "Tipos de semáforos"
        article_fold = _s_fold(article).replace(" ", "")
        norm_fold = _s_fold(norm).replace(" ", "")
        if "143" in article_fold and ("rgc" in norm_fold or not norm_fold):
            result["status"] = "document_precept_requires_review"
            result["requires_review"] = True
        else:
            result["status"] = "no_specific_mismatch_detected"
    elif fact_date:
        result["legal_rule_at_fact_date"] = "RGC art. 146.1.a — luz roja no intermitente"
        result["article_143_role_at_fact_date"] = "Semáforos cuadrados para vehículos o de carril"
        article_fold = _s_fold(article).replace(" ", "")
        if "143" in article_fold:
            result["status"] = "document_precept_requires_review"
            result["requires_review"] = True
        else:
            result["status"] = "no_specific_mismatch_detected"

    return result


def build_semaforo_legal_intelligence(core: Dict[str, Any]) -> Dict[str, Any]:
    """Construye inteligencia jurídica estructurada para multas de semáforo.

    Principios:
    - la extracción transcribe; este módulo interpreta;
    - nunca afirma que no existe prueba solo porque no esté en la copia analizada;
    - diferencia fotografía presente de secuencia/timing técnicamente acreditados;
    - conserva el precepto de la denuncia y lo compara con el marco vigente en la fecha;
    - no convierte una posible discordancia normativa en nulidad automática.
    """
    core = dict(core or {})
    secondary = _s_secondary(core)
    meta = _s_secondary_meta(core)
    evidence = meta.get("evidence") or {}

    doc_subject = secondary.get("document_subject")
    doc_subject = dict(doc_subject) if isinstance(doc_subject, dict) else {}
    ref = secondary.get("normative_reference")
    ref = dict(ref) if isinstance(ref, dict) else {}

    ordinary_fine = secondary.get("sancion_ordinaria_eur")
    if ordinary_fine in (None, ""):
        ordinary_fine = core.get("sancion_importe_eur")
    reduced_fine = secondary.get("importe_reducido_eur")
    points = core.get("puntos_detraccion")

    capture_method = _s_safe(secondary.get("capture_method")).strip() or None
    capture_automatic = (
        secondary.get("capture_automatic")
        if isinstance(secondary.get("capture_automatic"), bool)
        else None
    )
    photo_present = (
        secondary.get("vehicle_photo_present")
        if isinstance(secondary.get("vehicle_photo_present"), bool)
        else None
    )

    precept = _s_document_precept_analysis(core, secondary)

    sanction_consistency = {
        "expected_classification": "grave",
        "expected_standard_fine_eur": 200,
        "expected_points": 4,
        "expected_reduced_fine_eur_if_eligible": 100,
        "document_standard_fine_eur": ordinary_fine,
        "document_reduced_fine_eur": reduced_fine,
        "document_points": points,
        "standard_fine_matches": ordinary_fine == 200 if ordinary_fine is not None else None,
        "reduced_fine_matches": reduced_fine == 100 if reduced_fine is not None else None,
        "points_match": points == 4 if points is not None else None,
        "legal_conclusion_automatic": False,
    }

    issues: List[Dict[str, Any]] = []

    if precept.get("requires_review"):
        issues.append({
            "code": "DOCUMENT_PRECEPT_REQUIRES_REVIEW",
            "severity": "high",
            "message": (
                "El precepto transcrito en la notificación requiere revisión jurídica frente a la regulación "
                "vigente en la fecha del hecho. La posible discordancia no se convierte automáticamente en nulidad; "
                "debe comprobarse la subsunción y la motivación del expediente."
            ),
        })

    if capture_automatic is True or (capture_method and "cámara" in _s_fold(capture_method)):
        issues.append({
            "code": "AUTOMATIC_CAMERA_CAPTURE",
            "severity": "info",
            "message": (
                "La notificación atribuye la captación a cámara/vídeo. Debe revisarse la evidencia original "
                "y su trazabilidad temporal antes de concluir sobre la suficiencia probatoria."
            ),
        })

    if photo_present is True:
        issues.append({
            "code": "PHOTO_PRESENT_SEQUENCE_STATUS_UNKNOWN",
            "severity": "medium",
            "message": (
                "La copia analizada contiene fotografía del vehículo, pero la presencia de una imagen impresa no permite "
                "por sí sola determinar si constan la secuencia original, el instante de activación de la fase roja, "
                "el cruce de la línea de detención y los metadatos necesarios."
            ),
        })
    else:
        issues.append({
            "code": "GRAPHIC_EVIDENCE_STATUS_NOT_CONFIRMED",
            "severity": "medium",
            "message": (
                "No se ha podido confirmar desde los hechos estructurados qué soporte gráfico original integra el expediente. "
                "Debe comprobarse antes de formular una conclusión probatoria."
            ),
        })

    if not _s_safe(core.get("hecho_imputado")).strip():
        issues.append({
            "code": "FACT_LITERAL_MISSING",
            "severity": "high",
            "message": "No existe un hecho imputado literal suficientemente identificado para construir el borrador.",
        })

    if sanction_consistency["standard_fine_matches"] is False or sanction_consistency["points_match"] is False:
        issues.append({
            "code": "SANCTION_VALUES_REQUIRE_REVIEW",
            "severity": "high",
            "message": (
                "Los importes o puntos extraídos no coinciden con la consecuencia estándar esperable para una infracción "
                "de semáforo en rojo y requieren revisión antes de generar."
            ),
        })

    operator_review_reasons = [
        item["code"] for item in issues if item.get("severity") in {"high", "medium"}
    ]

    required_ok = all([
        _s_safe(core.get("expediente_ref")).strip(),
        _s_safe(core.get("matricula")).strip(),
        _s_safe(core.get("fecha_infraccion")).strip(),
        _s_safe(core.get("lugar_infraccion")).strip(),
        _s_safe(core.get("hecho_imputado")).strip(),
        ordinary_fine not in (None, ""),
        points not in (None, ""),
    ])

    return {
        "ok": True,
        "version": SEMAFORO_LEGAL_INTELLIGENCE_VERSION,
        "facts": {
            "expediente_ref": core.get("expediente_ref"),
            "matricula": core.get("matricula"),
            "fact_date": core.get("fecha_infraccion"),
            "fact_time": core.get("hora_infraccion"),
            "location": core.get("lugar_infraccion"),
            "hecho_imputado": core.get("hecho_imputado"),
            "organismo": core.get("organismo"),
            "ordinary_fine_eur": ordinary_fine,
            "reduced_fine_eur": reduced_fine,
            "points": points,
            "capture_method": capture_method,
            "capture_automatic": capture_automatic,
            "vehicle_photo_present": photo_present,
            "document_subject": {
                "full_name": _s_safe(doc_subject.get("full_name")).strip() or None,
                "id_number": _s_safe(doc_subject.get("id_number")).strip().upper() or None,
                "evidence": _s_safe(evidence.get("document_subject_name") or evidence.get("document_subject_id")),
            },
            "document_normative_reference": {
                "norm": _s_safe(ref.get("norm")).strip() or None,
                "article": _s_safe(ref.get("article")).strip() or None,
                "evidence": _s_safe(evidence.get("norma") or evidence.get("articulo")),
            },
            "fecha_emision": secondary.get("fecha_emision"),
            "fecha_limite_pago": secondary.get("fecha_limite_pago"),
        },
        "document_precept_analysis": precept,
        "sanction_consistency": sanction_consistency,
        "issues": issues,
        "draft_generation_allowed": bool(required_ok),
        "requires_operator_review": bool(operator_review_reasons),
        "operator_review_reasons": operator_review_reasons,
        "provenance": {
            "source": "validated_extraction+semaforo_secondary_facts",
            "secondary_facts_version": meta.get("version"),
            "secondary_facts_confidence": meta.get("confidence") or {},
            "secondary_facts_evidence": evidence,
            "automatic_legal_conclusion": False,
        },
    }


def build_semaforo_intelligence_template(core: Dict[str, Any]) -> Dict[str, str]:
    """Borrador jurídico específico alimentado por Semáforo Legal Intelligence."""
    core = dict(core or {})
    intel = (
        core.get("_semaforo_legal_intelligence")
        if isinstance(core.get("_semaforo_legal_intelligence"), dict)
        else build_semaforo_legal_intelligence(core)
    )
    facts = intel.get("facts") or {}
    precept = intel.get("document_precept_analysis") or {}

    hecho = _s_safe(facts.get("hecho_imputado")).strip() or "Hecho de semáforo pendiente de validación"
    organo = _s_safe(facts.get("organismo")).strip() or "órgano competente"
    expediente = _s_safe(facts.get("expediente_ref")).strip() or "[EXPEDIENTE]"
    matricula = _s_safe(facts.get("matricula")).strip() or "[MATRÍCULA]"
    fecha = _s_safe(facts.get("fact_date")).strip()
    hora = _s_safe(facts.get("fact_time")).strip()
    lugar = _s_safe(facts.get("location")).strip()
    capture_method = _s_safe(facts.get("capture_method")).strip()
    ordinary = facts.get("ordinary_fine_eur")
    reduced = facts.get("reduced_fine_eur")
    points = facts.get("points")
    doc_ref = facts.get("document_normative_reference") or {}

    datos = [
        "DATOS DOCUMENTALES RELEVANTES",
        f"• Hecho imputado: {hecho}",
        f"• Matrícula: {matricula}",
    ]
    if fecha:
        datos.append(f"• Fecha del hecho: {fecha}" + (f" · {hora}" if hora else ""))
    if lugar:
        datos.append(f"• Lugar: {lugar}")
    if capture_method:
        datos.append(f"• Sistema de captación indicado: {capture_method}")
    if ordinary is not None:
        datos.append(f"• Sanción ordinaria consignada: {ordinary} €")
    if reduced is not None:
        datos.append(f"• Importe reducido consignado: {reduced} €")
    if points is not None:
        datos.append(f"• Puntos consignados: {points}")
    if _s_safe(doc_ref.get("norm")).strip() or _s_safe(doc_ref.get("article")).strip():
        datos.append(
            "• Precepto transcrito en la notificación: "
            + " ".join(x for x in [_s_safe(doc_ref.get("norm")).strip(), _s_safe(doc_ref.get("article")).strip()] if x)
        )

    precept_block = ""
    if precept.get("requires_review"):
        precept_block = (
            "\n\nALEGACIÓN SEGUNDA — SUBSUNCIÓN NORMATIVA Y PRECEPTO CONSIGNADO\n\n"
            "La notificación debe identificar de forma clara el precepto aplicable y permitir comprobar la subsunción "
            "entre la conducta descrita y la norma invocada. En este expediente se ha transcrito del propio documento "
            f"la referencia «{_s_safe(doc_ref.get('norm')).strip()} {_s_safe(doc_ref.get('article')).strip()}». "
            f"Para la fecha del hecho, la regla material identificada por RTM para una luz roja circular se localiza en "
            f"{_s_safe(precept.get('legal_rule_at_fact_date')).strip()}, mientras que el artículo 143 se refiere a "
            f"{_s_safe(precept.get('article_143_role_at_fact_date')).strip()}. "
            "Esta circunstancia exige que la Administración aclare y motive la concreta base normativa utilizada. "
            "No se sostiene de forma automática la nulidad por la mera discordancia formal; se solicita comprobar "
            "si el expediente contiene una subsunción normativa correcta, suficiente y no generadora de indefensión."
        )
        next_num = "TERCERA"
    else:
        next_num = "SEGUNDA"

    body = (
        "A la atención del órgano competente,\n\n"
        f"Extracto literal del boletín:\n“{hecho}”\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRUEBA DE LA FASE ROJA, CRUCE DE LA LÍNEA DE DETENCIÓN Y TRAZABILIDAD\n\n"
        "La infracción exige acreditar que la luz roja no intermitente estaba activa y que el vehículo rebasó el semáforo "
        "o, cuando exista, la línea de detención anterior mientras dicha prohibición era efectiva. La documentación debe "
        "permitir reconstruir temporalmente esos extremos y vincularlos de forma inequívoca con el vehículo denunciado.\n\n"
        + "\n".join(datos)
        + precept_block
        + f"\n\nALEGACIÓN {next_num} — CAPTACIÓN POR CÁMARA Y EVIDENCIA ORIGINAL\n\n"
        + (
            "La propia notificación identifica un sistema de captación por cámara/vídeo. La copia analizada contiene una "
            "fotografía del vehículo, por lo que no se afirma la inexistencia de prueba gráfica. Lo que debe comprobarse es "
            "si el expediente incorpora el soporte original suficiente para verificar la secuencia temporal, el estado de la "
            "señal, el instante de cruce de la línea de detención, la fecha y hora, la integridad del registro y la asignación "
            "inequívoca al vehículo."
            if facts.get("vehicle_photo_present") is True
            else
            "La situación de la evidencia gráfica original no puede determinarse únicamente con los hechos estructurados "
            "disponibles. Debe comprobarse en el expediente qué soporte de imagen o vídeo existe y si permite verificar de "
            "forma suficiente la fase roja, la línea de detención y la identificación del vehículo."
        )
        + "\n\nALEGACIÓN CUARTA — PROPOSICIÓN DE PRUEBA Y MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "Se solicita el acceso e incorporación de la evidencia original y de los datos técnicos necesarios para comprobar "
        "el hecho imputado. La resolución debe valorar expresamente la prueba practicada, fijar los hechos acreditados y "
        "explicar la concreta subsunción normativa y la consecuencia sancionadora aplicada."
    )

    return {
        "asunto": "ESCRITO DE ALEGACIONES — SEMÁFORO EN ROJO",
        "cuerpo": body.strip(),
    }
