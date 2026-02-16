import json
import hashlib
import mimetypes
import re
from typing import Any, Dict, List, Tuple, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_original
from openai_vision import extract_from_image_bytes
from text_extractors import (
    extract_text_from_pdf_bytes,
    extract_text_from_docx_bytes,
    has_enough_text,
)
from openai_text import extract_from_text

router = APIRouter(tags=["analyze"])

DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _flatten_text(extracted_core: Dict[str, Any], text_content: str = "") -> str:
    parts: List[str] = []
    if isinstance(extracted_core, dict):
        for k, v in extracted_core.items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                parts.append(f"{k}: {v}")
            else:
                try:
                    parts.append(f"{k}: {str(v)}")
                except Exception:
                    pass
    if text_content:
        parts.append(text_content)
    return "\n".join(parts)


def _extract_precepts(text_blob: str) -> Dict[str, Any]:
    """
    Extrae referencias normativas/preceptos con heurística robusta.

    Devuelve:
      - preceptos_detectados: lista de strings
      - articulo_num: int|None
      - apartado_num: int|None
      - norma_hint: string|None
    """
    t = (text_blob or "").lower()

    precepts: List[str] = []
    art_num: Optional[int] = None
    apt_num: Optional[int] = None

    # 1) Patrones explícitos "artículo 18" / "art. 18" (admite ceros a la izquierda)
    m_art = re.search(r"\bart[ií]culo\s*0?(\d{1,3})\b", t) or re.search(r"\bart\.\s*0?(\d{1,3})\b", t)
    if m_art:
        try:
            art_num = int(m_art.group(1))
            precepts.append(f"articulo {art_num}")
        except Exception:
            art_num = None

    # 2) Patrones explícitos "apartado 1" / "aptdo. 1"
    m_apt = re.search(r"\bapartado\s*(\d{1,3})\b", t) or re.search(r"\baptdo\.?\s*(\d{1,3})\b", t)
    if m_apt:
        try:
            apt_num = int(m_apt.group(1))
            if art_num is not None:
                precepts.append(f"articulo {art_num} apartado {apt_num}")
            else:
                precepts.append(f"apartado {apt_num}")
        except Exception:
            apt_num = None

    # 3) Patrón muy frecuente en boletines DGT: "052.1" / "52.1" (artículo.apartado)
    m_code = re.search(r"\b0?(\d{1,3})\.(\d{1,3})\b", t)
    if m_code:
        try:
            art_from_code = int(m_code.group(1))
            apt_from_code = int(m_code.group(2))
            if art_num is None:
                art_num = art_from_code
                precepts.append(f"articulo {art_num}")
            if apt_num is None:
                apt_num = apt_from_code
            if art_num is not None and apt_num is not None:
                precepts.append(f"articulo {art_num} apartado {apt_num}")
        except Exception:
            pass

    # 4) Patrón contextual: "precepto infringido ... artículo X ... apartado Y"
    m_ctx = re.search(
        r"precepto\s+infringido[\s\S]{0,160}?art[ií]culo\s*0?(\d{1,3})[\s\S]{0,120}?(?:apartado|aptdo\.?)\s*(\d{1,3})",
        t
    )
    if m_ctx:
        try:
            art_ctx = int(m_ctx.group(1))
            apt_ctx = int(m_ctx.group(2))
            if art_num is None:
                art_num = art_ctx
                precepts.append(f"articulo {art_num}")
            if apt_num is None:
                apt_num = apt_ctx
            if art_num is not None and apt_num is not None:
                precepts.append(f"articulo {art_num} apartado {apt_num}")
        except Exception:
            pass

    # 5) Corrección conservadora de OCR (caso típico: 80 leído en vez de 18)
    if art_num == 80 and ("atención permanente" in t or "atencion permanente" in t) and ("precepto infringido" in t) and ("cir" in t or "reglamento general de circul" in t):
        art_num = 18
        precepts.append("articulo 18")
        if apt_num is not None:
            precepts.append(f"articulo 18 apartado {apt_num}")

    # Normas típicas
    norma_hint: Optional[str] = None

    if ("r.d. legislativo" in t and "8/2004" in t) or ("rd legislativo" in t and "8/2004" in t) or ("8/2004" in t and "responsabilidad civil" in t):
        norma_hint = "RDL 8/2004"
        precepts.append("RDL 8/2004")

    if "2822/98" in t or "r.d. 2822/98" in t or "rd 2822/98" in t:
        norma_hint = norma_hint or "RD 2822/98"
        precepts.append("RD 2822/98")

    if "reglamento general de circul" in t or "rgc" in t:
        precepts.append("Reglamento General de Circulación")

    if "ley sobre tráfico" in t or "ley de trafico" in t or "trltsv" in t:
        precepts.append("Ley de Tráfico (genérico)")

    if "lsoa" in t:
        norma_hint = norma_hint or "LSOA"
        precepts.append("LSOA")

    # Normalizar únicos
    seen = set()
    uniq: List[str] = []
    for p in precepts:
        pp = (p or "").strip()
        if pp and pp not in seen:
            seen.add(pp)
            uniq.append(pp)

    return {
        "preceptos_detectados": uniq,
        "articulo_num": art_num,
        "apartado_num": apt_num,
        "norma_hint": norma_hint,
    }


def _detect_facts_and_type(text_blob: str) -> Tuple[str, str, List[str]]:
    t = (text_blob or "").lower()
    facts: List[str] = []

    # Seguro (LSOA / RDL 8/2004)
    if ("lsoa" in t) or (("r.d. legislativo" in t or "rd legislativo" in t) and "8/2004" in t):
        facts.append("CARENCIA DE SEGURO OBLIGATORIO")
        return ("seguro", facts[0], facts)

    # No identificar (art. 9.1 bis)
    if re.search(r"\bart\.\s*9\.?\s*1\s*bis\b", t) or re.search(r"\bart[ií]culo\s*9\.?\s*1\s*bis\b", t):
        facts.append("NO IDENTIFICAR AL CONDUCTOR (ART. 9.1 BIS)")
        return ("no_identificar", facts[0], facts)

    # Condiciones del vehículo (art. 12 / RD 2822/98)
    if re.search(r"\bart[ií]culo\s*12\b", t) or re.search(r"\bart\.\s*12\b", t) or ("2822/98" in t):
        facts.append("INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO")
        return ("condiciones_vehiculo", facts[0], facts)

    # Alumbrado / señalización óptica (art. 15)
    if re.search(r"\bart[ií]culo\s*15\b", t) or re.search(r"\bart\.\s*15\b", t):
        facts.append("DEFECTOS EN ALUMBRADO/SEÑALIZACIÓN ÓPTICA (ART. 15)")
        return ("condiciones_vehiculo", facts[0], facts)

    # Atención permanente (art. 18)
    if re.search(r"\bart[ií]culo\s*18\b", t) or re.search(r"\bart\.\s*18\b", t):
        facts.append("NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN (ART. 18)")
        return ("atencion", facts[0], facts)

    # Marcas viales (art. 167)
    if re.search(r"\bart[ií]culo\s*167\b", t) or re.search(r"\bart\.\s*167\b", t):
        facts.append("NO RESPETAR MARCA LONGITUDINAL CONTINUA (ART. 167)")
        return ("marcas_viales", facts[0], facts)

    # Semáforo (evitar 'luz roja' suelta)
    sema_patterns = [
        r"circular\s+con\s+luz\s+roja",
        r"sem[aá]foro",
        r"fase\s+roja",
        r"luz\s+roja\s+del\s+sem[aá]foro",
        r"no\s+respetar\s+la\s+luz\s+roja",
        r"no\s+respetar\s+.*sem[aá]foro",
    ]
    for ptn in sema_patterns:
        if re.search(ptn, t):
            facts.append("CIRCULAR CON LUZ ROJA")
            return ("semaforo", facts[0], facts)

    # Velocidad
    m = re.search(r"\b(\d{2,3})\s*km\s*/?\s*h\b", t)
    if m:
        vel = m.group(1)
        facts.append(f"EXCESO DE VELOCIDAD ({vel} km/h)")
        return ("velocidad", facts[0], facts)
    if "exceso de velocidad" in t or "radar" in t or "cinemómetro" in t or "cinemometro" in t:
        facts.append("EXCESO DE VELOCIDAD")
        return ("velocidad", facts[0], facts)

    # Marcas viales (texto)
    if ("marca longitudinal continua" in t) or ("marca longitudinal" in t and "continua" in t) or ("línea continua" in t) or ("linea continua" in t):
        facts.append("NO RESPETAR MARCA LONGITUDINAL CONTINUA")
        return ("marcas_viales", facts[0], facts)
    if ("adelant" in t) and (("línea continua" in t) or ("linea continua" in t) or ("marca longitudinal" in t)):
        facts.append("ADELANTAMIENTO INDEBIDO CON LÍNEA CONTINUA")
        return ("marcas_viales", facts[0], facts)

    # Móvil
    if "utilizando manualmente" in t and ("teléfono" in t or "telefono" in t or "móvil" in t or "movil" in t):
        facts.append("USO MANUAL DEL TELÉFONO MÓVIL")
        return ("movil", facts[0], facts)
    if "teléfono móvil" in t or "telefono movil" in t or "uso del teléfono" in t or "uso del telefono" in t:
        facts.append("USO DEL TELÉFONO MÓVIL")
        return ("movil", facts[0], facts)

    # No identificar (texto)
    if ("identificar" in t and "conductor" in t) and ("plazo de veinte" in t or "20" in t or "veinte días" in t or "veinte dias" in t):
        facts.append("NO IDENTIFICAR AL CONDUCTOR")
        return ("no_identificar", facts[0], facts)

    # ITV / Seguro (texto)
    if ("itv" in t and ("caduc" in t or "sin itv" in t or "no vigente" in t)) or ("inspección técnica" in t and ("caduc" in t or "no vigente" in t)):
        facts.append("ITV NO VIGENTE / CADUCADA")
        return ("itv", facts[0], facts)

    if (
        ("seguro" in t and ("obligatorio" in t or "carece" in t or "sin seguro" in t))
        or ("contrato de seguro" in t and ("mantenga en vigor" in t or "mantener en vigor" in t or "suscrito" in t or "suscrita" in t))
        or ("sin que conste" in t and "seguro" in t)
        or ("responsabilidad civil derivada de su circulación" in t or "responsabilidad civil derivada de su circulacion" in t)
        or ("8/2004" in t and ("responsabilidad civil" in t or "seguro" in t))
    ):
        facts.append("CARENCIA DE SEGURO OBLIGATORIO")
        return ("seguro", facts[0], facts)

    # Alcoholemia / drogas
    if "alcoholemia" in t or "mg/l" in t or "aire espirado" in t:
        facts.append("ALCOHOLEMIA")
        return ("alcoholemia", facts[0], facts)
    if "drogas" in t or "estupefac" in t or "cannabis" in t or "cocaína" in t or "cocaina" in t:
        facts.append("CONDUCCIÓN BAJO EFECTOS DE DROGAS")
        return ("drogas", facts[0], facts)

    # Cinturón / casco / SRI
    if "cinturón" in t or "cinturon" in t:
        facts.append("NO UTILIZAR CINTURÓN DE SEGURIDAD")
        return ("cinturon", facts[0], facts)
    if "casco" in t:
        facts.append("NO UTILIZAR CASCO")
        return ("casco", facts[0], facts)
    if "sri" in t or "sistema de retención infantil" in t or "retención infantil" in t:
        facts.append("SISTEMA DE RETENCIÓN INFANTIL (SRI)")
        return ("sri", facts[0], facts)

    # Condiciones vehículo (texto)
    if any(k in t for k in [
        "condiciones reglamentarias", "vehículo reseñado", "vehiculo reseñado",
        "deslumbr", "dispositivos de alumbrado", "señalización óptica", "senalizacion optica",
        "anexo i", "emite luz", "destellos", "luz roja en la parte trasera"
    ]):
        facts.append("INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO")
        return ("condiciones_vehiculo", facts[0], facts)

    # Atención (texto)
    if "atención permanente" in t or "atencion permanente" in t or "distracción" in t or "distraccion" in t:
        facts.append("NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN")
        return ("atencion", facts[0], facts)

    # Parking
    if "doble fila" in t:
        facts.append("ESTACIONAR EN DOBLE FILA")
        return ("parking", facts[0], facts)
    if "minusválid" in t or "minusvalid" in t or "pmr" in t:
        facts.append("ESTACIONAR EN PLAZA RESERVADA (PMR)")
        return ("parking", facts[0], facts)
    if "zona azul" in t or "ora" in t or "ticket" in t:
        facts.append("ESTACIONAMIENTO REGULADO (ZONA ORA)")
        return ("parking", facts[0], facts)

    return ("otro", "", [])


def _enrich_with_triage(extracted_core: Dict[str, Any], text_blob: str) -> Dict[str, Any]:
    tipo, hecho, facts = _detect_facts_and_type(text_blob)
    out = dict(extracted_core or {})
    out["tipo_infraccion"] = tipo
    out["hecho_imputado"] = hecho or None
    out["facts_phrases"] = facts
    pre = _extract_precepts(text_blob)
    out["preceptos_detectados"] = pre.get("preceptos_detectados") or []
    out["articulo_infringido_num"] = pre.get("articulo_num")
    out["apartado_infringido_num"] = pre.get("apartado_num")
    out["norma_hint"] = pre.get("norma_hint")
    return out


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Archivo vacío.")
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 12MB).")

        sha256 = _sha256_bytes(content)
        mime = file.content_type or (mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream")
        size_bytes = len(content)

        engine = get_engine()

        with engine.begin() as conn:
            case_id = conn.execute(
                text("INSERT INTO cases(status, created_at, updated_at) VALUES ('uploaded', NOW(), NOW()) RETURNING id")
            ).scalar()

            b2_bucket, b2_key = upload_original(str(case_id), content, file.filename, mime)

            conn.execute(
                text("INSERT INTO documents (case_id, kind, b2_bucket, b2_key, sha256, mime, size_bytes, created_at) VALUES (:case_id, 'original', :b2_bucket, :b2_key, :sha256, :mime, :size_bytes, NOW())"),
                {"case_id": case_id, "b2_bucket": b2_bucket, "b2_key": b2_key, "sha256": sha256, "mime": mime, "size_bytes": size_bytes},
            )

            model_used = "mock"
            confidence = 0.1
            extracted_core: Dict[str, Any] = {}
            text_content = ""

            if mime.startswith("image/"):
                extracted_core = extract_from_image_bytes(content, mime, file.filename)
                model_used = "openai_vision"
                confidence = 0.7

            elif mime == "application/pdf":
                text_content = extract_text_from_pdf_bytes(content)
                if has_enough_text(text_content):
                    extracted_core = extract_from_text(text_content)
                    model_used = "openai_text"
                    confidence = 0.8
                else:
                    extracted_core = extract_from_image_bytes(content, mime, file.filename)
                    model_used = "openai_vision"
                    confidence = 0.6

            elif mime in DOCX_MIMES:
                text_content = extract_text_from_docx_bytes(content)
                if has_enough_text(text_content):
                    extracted_core = extract_from_text(text_content)
                    model_used = "openai_text"
                    confidence = 0.8
                else:
                    extracted_core = {
                        "organismo": None,
                        "expediente_ref": None,
                        "importe": None,
                        "fecha_notificacion": None,
                        "fecha_documento": None,
                        "tipo_sancion": None,
                        "pone_fin_via_administrativa": None,
                        "plazo_recurso_sugerido": None,
                        "observaciones": "DOCX sin texto suficiente.",
                    }

            else:
                extracted_core = {
                    "organismo": None,
                    "expediente_ref": None,
                    "importe": None,
                    "fecha_notificacion": None,
                    "fecha_documento": None,
                    "tipo_sancion": None,
                    "pone_fin_via_administrativa": None,
                    "plazo_recurso_sugerido": None,
                    "observaciones": "Tipo de archivo no soportado.",
                }

            blob = _flatten_text(extracted_core, text_content=text_content)
            extracted_core = _enrich_with_triage(extracted_core, blob)

            wrapper = {
                "filename": file.filename,
                "mime": mime,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "storage": {"bucket": b2_bucket, "key": b2_key},
                "extracted": extracted_core,
            }

            conn.execute(
                text("INSERT INTO extractions (case_id, extracted_json, confidence, model, created_at) VALUES (:case_id, CAST(:json AS JSONB), :confidence, :model, NOW())"),
                {"case_id": case_id, "json": json.dumps(wrapper, ensure_ascii=False), "confidence": confidence, "model": model_used},
            )

            conn.execute(
                text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:case_id, 'analyze_ok', CAST(:payload AS JSONB), NOW())"),
                {"case_id": case_id, "payload": json.dumps({"model": model_used, "confidence": confidence})},
            )

            conn.execute(text("UPDATE cases SET status='analyzed', updated_at=NOW() WHERE id=:case_id"), {"case_id": case_id})

        return {"ok": True, "message": "Análisis completo generado.", "case_id": str(case_id), "extracted": wrapper}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en /analyze: {e}")
