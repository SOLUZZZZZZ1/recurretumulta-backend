import json
import os
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from database import get_engine
from openai import OpenAI

from ai.text_loader import load_text_from_b2
from ai.prompts.classify_documents import PROMPT as PROMPT_CLASSIFY
from ai.prompts.timeline_builder import PROMPT as PROMPT_TIMELINE
from ai.prompts.procedure_phase import PROMPT as PROMPT_PHASE
from ai.prompts.admissibility_guard import PROMPT as PROMPT_GUARD
from ai.prompts.draft_recurso_v2 import PROMPT as PROMPT_DRAFT
from ai.prompts.module_semaforo import module_semaforo

MAX_EXCERPT_CHARS = 12000

PROMPT_DRAFT_REPAIR_VELOCIDAD = """
Eres abogado especialista en sancionador (España). Debes REPARAR un borrador de recurso por EXCESO DE VELOCIDAD.

OBJETIVO: reescribir el borrador COMPLETO para que pase una validación estricta.

REGLAS OBLIGATORIAS:
1) La PRIMERA ALEGACIÓN NO puede ser 'Presunción de inocencia'.
2) La PRIMERA ALEGACIÓN debe titularse exactamente:
   'ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)'
3) El cuerpo debe contener literalmente la expresión: 'cadena de custodia'.
4) Debe incluir 'margen' y 'velocidad corregida'.
5) Debe exigir 'certificado' y 'verificación' (metrológica) del cinemómetro.
6) Debe exigir 'captura' o 'fotograma' completo.
7) No inventes hechos. Mantén prudencia: 'no consta acreditado', 'no se aporta'.

ENTRADA: JSON con borrador anterior y contexto.
SALIDA: SOLO JSON con la misma forma {asunto,cuerpo,variables_usadas,checks,notes_for_operator}.
"""

def _velocity_strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing = []
    if "cadena de custodia" not in b:
        missing.append("cadena_custodia")
    # primera alegación
    first = ""
    for line in (body or "").splitlines():
        l = (line or "").strip()
        if l.lower().startswith("alegación") or l.lower().startswith("alegacion"):
            first = l.lower()
            break
    if first and ("presunción" in first or "presuncion" in first or "inocencia" in first):
        missing.append("orden_alegaciones")
    # mínimos VSE
    for key, needles in {
        "margen": ["margen"],
        "velocidad_corregida": ["velocidad corregida", "corregida"],
        "metrologia": ["certificado", "verificación", "verificacion"],
        "cinemometro": ["cinemómetro", "cinemometro", "radar"],
        "captura": ["captura", "fotograma", "imagen"],
    }.items():
        if not any(n in b for n in needles):
            missing.append(key)
    # unique
    seen=set(); out=[]
    for x in missing:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def _force_velocity_asunto(draft: Dict[str, Any]) -> None:
    try:
        draft["asunto"] = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    except Exception:
        pass


def _force_velocity_first_title(body: str) -> str:
    if not body:
        return body
    target = "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)"
    body = re.sub(r"ALEGACIÓN\s+PRIMERA\s+—\s+INSUFICIENCIA\s+PROBATORIA\s+ESPECÍFICA\s+DEL\s+TIPO", target, body, flags=re.IGNORECASE)
    body = re.sub(r"ALEGACIÓN\s+PRIMERA\s+—\s+INSUFICIENCIA\s+PROBATORIA\s+ESPECIFICA\s+DEL\s+TIPO", target, body, flags=re.IGNORECASE)
    return body


def _remove_tipicity_intruder_in_speed(body: str) -> str:
    if not body:
        return body
    body = re.sub(r"Se pone de manifiesto[\s\S]*?(?=\nIII\.\s*SOLICITO)", "", body, flags=re.IGNORECASE)
    body = re.sub(r"[^\n]*subsunción típica[\s\S]*$", "", body, flags=re.IGNORECASE)
    body = re.sub(r"[^\n]*tipicidad[\s\S]*$", "", body, flags=re.IGNORECASE)
    body = re.sub(r"[^\n]*artículo\s+300[\s\S]*$", "", body, flags=re.IGNORECASE)
    return body


def _fix_solicito_format(body: str) -> str:
    if not body:
        return body
    body = re.sub(r"(III\.\s*SOLICITO)\s*(?=1[\)\.])", r"\1\n", body, flags=re.IGNORECASE)
    body = re.sub(r"(\n1[\)\.][^\n]*?)\s*2[\)\.]", r"\1\n2)", body)
    body = re.sub(r"(\n2\)[^\n]*?)\s*3[\)\.]", r"\1\n3)", body)
    body = re.sub(r"(\.\s*)3[\)\.]", r"\1\n3)", body)
    return body



def _fix_solicito_newline(body: str) -> str:
    if not body:
        return body
    return re.sub(r"(III\.\s*SOLICITO)\s*(?=1\))", r"\1\n", body, flags=re.IGNORECASE)


def _force_archivo_in_speed_body(body: str) -> str:
    if not body:
        return body
    reps = [
        ("Que se acuerde la revisión del expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la REVISIÓN del expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la revisión del Expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la REVISIÓN del Expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la revisión del expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la REVISIÓN del expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la revisión del Expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la REVISIÓN del Expediente", "2) Que se acuerde el ARCHIVO del expediente"),
    ]
    for a, b in reps:
        body = body.replace(a, b)
    return body


def _ensure_speed_antecedentes(body: str, velocity_calc: Dict[str, Any]) -> str:
    try:
        if not body or not (velocity_calc or {}).get("ok"):
            return body
        measured = velocity_calc.get("measured")
        if not isinstance(measured, int):
            return body
        body = re.sub(
            r"(Hecho imputado:\s*EXCESO DE VELOCIDAD)\s*(?:\([^)]+\))?\s*\.",
            rf"\1 ({measured} km/h).",
            body,
            flags=re.IGNORECASE,
        )
        return body
    except Exception:
        return body


def _ensure_velocity_calc_paragraph(body: str, velocity_calc: Dict[str, Any]) -> str:
    try:
        if not body or not (velocity_calc or {}).get("ok"):
            return body
        if "a efectos ilustrativos" in body.lower() and "velocidad corregida" in body.lower():
            return body

        limit = velocity_calc.get("limit")
        measured = velocity_calc.get("measured")
        corrected = velocity_calc.get("corrected")
        expected = velocity_calc.get("expected") or {}
        band = expected.get("band")
        fine = expected.get("fine")
        pts = expected.get("points")

        parts = ["A efectos ilustrativos,"]
        if isinstance(limit, int) and isinstance(measured, int):
            parts.append(f"con un límite de {limit} km/h y una medición de {measured} km/h,")
        if isinstance(corrected, (int, float)):
            parts.append(f"la aplicación del margen situaría la velocidad corregida en torno a {float(corrected):.2f} km/h,")
        parts.append("extremo cuya acreditación corresponde a la Administración (margen aplicado, velocidad corregida y banda/tramo resultante).")
        if band:
            tail = f"De acuerdo con la tabla orientativa, ello podría encajar en la banda: {band}"
            if isinstance(fine, int) or isinstance(pts, int):
                tail += f" (multa {fine}€ / puntos {pts})."
            else:
                tail += "."
            parts.append(tail)

        paragraph = " ".join(parts)
        mm = re.search(r"(ALEGACIÓN\s+PRIMERA[^\n]*\n)", body, flags=re.IGNORECASE)
        if mm:
            i = mm.end(1)
            return body[:i] + paragraph + "\n" + body[i:]
        return re.sub(r"(\nIII\.\s*SOLICITO)", "\n" + paragraph + r"\n\1", body, flags=re.IGNORECASE)
    except Exception:
        return body


def _velocity_pro_enrich(body: str, velocity_calc: Dict[str, Any]) -> str:
    if not body:
        return body
    b = body.lower()
    too_short = len(body.strip()) < 1400
    must_have = ["orden ict/155/2020", "control metrol", "cadena de custodia", "velocidad corregida", "margen", "cinemómetro", "certificado", "verificación", "captura"]
    missing_key = any(k not in b for k in must_have)
    if not (too_short or missing_key):
        return body

    calc_sentence = ""
    if "a efectos ilustrativos" in b:
        calc_sentence = ""
    elif (velocity_calc or {}).get("ok"):
        corrected = velocity_calc.get("corrected")
        limit = velocity_calc.get("limit")
        measured = velocity_calc.get("measured")
        expected = (velocity_calc.get("expected") or {})
        band = expected.get("band")
        fine = expected.get("fine")
        pts = expected.get("points")
        if isinstance(limit, int) and isinstance(measured, int) and isinstance(corrected, (int, float)):
            calc_sentence = f"A efectos ilustrativos, con un límite de {limit} km/h y una medición de {measured} km/h, la aplicación del margen legal podría situar la velocidad corregida en torno a {float(corrected):.2f} km/h."
            if band:
                calc_sentence += f" Ello podría encajar en la banda: {band}"
                if isinstance(fine, int) or isinstance(pts, int):
                    calc_sentence += f" (multa {fine}€ / puntos {pts})."
                else:
                    calc_sentence += "."
        else:
            calc_sentence = "A efectos ilustrativos, la aplicación del margen legal puede alterar la velocidad corregida y, por tanto, el tramo sancionador; corresponde a la Administración acreditar margen aplicado, velocidad corregida y banda/tramo resultante."

    pro_body = f"""\nLa validez de una sanción por exceso de velocidad basada en cinemómetro exige la acreditación documental del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020). No basta una afirmación genérica de verificación: debe aportarse soporte documental verificable.\n\nNo consta acreditado en el expediente:\n\n1) Identificación completa del cinemómetro utilizado (marca, modelo y número de serie) y emplazamiento exacto (vía, punto kilométrico y sentido).\n2) Certificado de verificación metrológica vigente a la fecha del hecho, así como constancia de la última verificación periódica o, en su caso, tras reparación.\n3) Captura o fotograma COMPLETO, sin recortes y legible, que permita asociar inequívocamente la medición al vehículo denunciado.\n4) Margen aplicado y determinación de la velocidad corregida (velocidad medida vs velocidad corregida), con motivación técnica suficiente.\n5) Acreditación de la cadena de custodia del dato (integridad del registro, sistema de almacenamiento y correspondencia inequívoca con el vehículo).\n6) Acreditación del límite aplicable y su señalización en el punto exacto (genérica vs específica) y su coherencia con la ubicación consignada.\n7) Motivación técnica individualizada que vincule medición, margen aplicado, velocidad corregida y tramo sancionador resultante.\n\n{calc_sentence}\n"""

    if re.search(r"ALEGACIÓN\s+PRIMERA", body, flags=re.IGNORECASE):
        if "orden ict/155/2020" in b and "1)" in b and "7)" in b and "cadena de custodia" in b:
            return body
        mm = re.search(r"(ALEGACIÓN\s+PRIMERA[^\n]*\n)", body, flags=re.IGNORECASE)
        if mm:
            i = mm.end(1)
            return body[:i] + pro_body.strip() + "\n" + body[i:]
        return body

    return re.sub(r"(\nIII\.\s*SOLICITO)", "\nALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)\n" + pro_body.strip() + r"\n\1", body, flags=re.IGNORECASE)


def _normalize_velocity_titles_and_remove_tipicity(body: str, attack_plan: Dict[str, Any]) -> str:
    if not body:
        return body
    inf = ((attack_plan or {}).get("infraction_type") or "").lower().strip()
    meta = (attack_plan or {}).get("meta") or {}
    has_mismatch = isinstance(meta, dict) and ("tipicity_mismatch" in meta)
    if inf != "velocidad" or has_mismatch:
        return body

    body = re.sub(
        r"ALEGACIÓN\s+PRIMERA\s+—\s+VULNERACIÓN\s+DEL\s+PRINCIPIO\s+DE\s+TIPICIDAD\s+Y\s+SUBSUNCIÓN",
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)",
        body,
        flags=re.IGNORECASE,
    )
    for ptn in [r"[^\n]*subsunción típica[\s\S]*$", r"[^\n]*artículo\s+300[\s\S]*$", r"[^\n]*tipicidad y subsunción[\s\S]*$"]:
        body = re.sub(ptn, "", body, flags=re.IGNORECASE)
    return body.strip() + "\n"



def _override_mode() -> str:
    """Controla el modo de override para pruebas.
    Valores:
      - TEST_REALISTA: fuerza admisibilidad para poder generar, pero mantiene contexto (antigüedad, etc.)
      - SANDBOX_DEMO: fuerza admisibilidad y normaliza el contexto para simular un caso 'actual' en demos internas.
    Se configura por variable de entorno RTM_OVERRIDE_MODE.
    """
    m = (os.getenv("RTM_OVERRIDE_MODE") or "TEST_REALISTA").strip().upper()
    if m not in ("TEST_REALISTA", "SANDBOX_DEMO"):
        m = "TEST_REALISTA"
    return m

def _sanitize_for_sandbox_demo(attack_plan: Dict[str, Any]) -> Dict[str, Any]:
    """Elimina módulos claramente ligados a antigüedad/prescripción para pruebas tipo demo.
    No inventa hechos; simplemente evita argumentar sobre fechas pasadas cuando el objetivo es probar el generador.
    """
    plan = dict(attack_plan or {})
    sec = plan.get("secondary") or []
    if isinstance(sec, list):
        sec2 = []
        for item in sec:
            title = (item or {}).get("title") if isinstance(item, dict) else ""
            tl = (title or "").lower()
            # Quitar módulos de 'antigüedad', 'actos interruptivos', 'firmeza' típicos de expedientes antiguos
            if any(k in tl for k in ["antigüedad", "actos interrupt", "firmeza", "notificación válida", "notificacion valida"]):
                continue
            sec2.append(item)
        plan["secondary"] = sec2

    pr = plan.get("proof_requests") or []
    if isinstance(pr, list):
        pr2 = []
        for x in pr:
            xl = (x or "").lower()
            if any(k in xl for k in ["actuaciones interrupt", "firmeza", "estado actual del expediente", "acreditación de la notificación", "acreditacion de la notificacion"]):
                continue
            pr2.append(x)
        plan["proof_requests"] = pr2

    plan.setdefault("meta", {})
    plan["meta"]["sandbox_demo_sanitized"] = True
    return plan


def _extract_speed_pair_from_blob(blob: str) -> Dict[str, Any]:
    """Extrae velocidad medida y límite de forma robusta (OCR/texto libre).

    Soporta patrones típicos DGT:
      - "CIRCULAR A 123 KM/H, ESTANDO LIMITADA ... A 90 KM/H"
      - "circulaba a 76 km/h ... limitada a 50 km/h"
      - "velocidad: 123 km/h" y "límite: 90 km/h"
      - Variantes con "limitada la velocidad a", "límite de", "limite", etc.

    Devuelve:
      - measured: int|None
      - limit: int|None
      - confidence: float (0..1)
      - raw_hits: lista de coincidencias
    """
    t = (blob or "").replace("\n", " ").replace("\t", " ").lower()
    t = re.sub(r"\s+", " ", t).strip()

    measured = None
    limit = None
    hits = []

    # 1) Patrón fuerte: "circular a X km/h ... limitada ... a Y km/h"
    p_strong = r"circular\s+a\s+(\d{2,3})\s*km\s*/?h[\s\S]{0,120}?(?:limitad[ao]a?|limitada\s+la\s+velocidad|l[ií]mite|limite)[^\d]{0,40}(\d{2,3})\s*km\s*/?h"
    ms = re.search(p_strong, t)
    if ms:
        try:
            measured = int(ms.group(1))
            limit = int(ms.group(2))
            hits.append(("strong", measured, limit))
        except Exception:
            pass

    # 2) "a X km/h" (medida) + "limitada ... a Y km/h" (límite) por separado
    if measured is None:
        m1 = re.search(r"\b(?:circular|circulaba|circulando)\s+a\s+(\d{2,3})\s*km\s*/?h\b", t)
        if m1:
            try:
                measured = int(m1.group(1)); hits.append(("measured_phrase", measured))
            except Exception:
                measured = None

    if limit is None:
        m2 = re.search(r"\b(?:limitad[ao]a?|limitada\s+la\s+velocidad|l[ií]mite|limite)\b[^\d]{0,40}(\d{2,3})\s*km\s*/?h\b", t)
        if m2:
            try:
                limit = int(m2.group(1)); hits.append(("limit_phrase", limit))
            except Exception:
                limit = None

    # 3) "velocidad X km/h" + "límite Y km/h"
    if measured is None:
        m3 = re.search(r"\bvelocidad\b[^\d]{0,20}(\d{2,3})\s*km\s*/?h\b", t)
        if m3:
            try:
                measured = int(m3.group(1)); hits.append(("measured_velocidad", measured))
            except Exception:
                measured = None

    if limit is None:
        m4 = re.search(r"\b(?:l[ií]mite|limite)\b[^\d]{0,20}(\d{2,3})\s*km\s*/?h\b", t)
        if m4:
            try:
                limit = int(m4.group(1)); hits.append(("limit_limite", limit))
            except Exception:
                limit = None

    # 4) Fallback inteligente: dos o más velocidades => mayor=medida, menor=límite (si coherente)
    if measured is None or limit is None:
        nums = [int(x) for x in re.findall(r"\b(\d{2,3})\s*km\s*/?h\b", t)]
        nums = [n for n in nums if 10 <= n <= 250]
        if len(nums) >= 2:
            hi = max(nums)
            lo = min(nums)
            if measured is None:
                measured = hi
            if limit is None:
                limit = lo
            hits.append(("fallback_pair", hi, lo, nums))

    # Normalización y confianza
    conf = 0.0
    if isinstance(measured, int):
        conf += 0.45
    if isinstance(limit, int):
        conf += 0.45
    if isinstance(measured, int) and isinstance(limit, int) and 20 <= limit <= 130 and measured >= limit:
        conf += 0.10

    # Si el fallback usó 2+ números pero no hay palabras clave, bajamos un poco la confianza
    if hits and hits[-1][0] == "fallback_pair" and not any(k in t for k in ["limitad", "límite", "limite", "velocidad", "circular", "circulaba"]):
        conf = max(0.5, conf - 0.1)

    conf = round(min(conf, 1.0), 2)

    return {"measured": measured, "limit": limit, "confidence": conf, "raw_hits": hits}

def _speed_margin_value(measured: int, capture_mode: str) -> float:
    """Margen conservador según Orden ICT/155/2020 (verificación periódica) para cinemómetros en servicio.
    - Instalación fija/estática: ±5 km/h (v<=100), ±5% (v>100)
    - Instalación móvil sobre vehículo: ±7 km/h (v<=100), ±7% (v>100)
    Si no se conoce modo, usa fijo/estático (más favorable al denunciado) como default.
    """
    cm = (capture_mode or "").upper()
    mobile = (cm == "MOBILE") or (cm == "MOVING") or (cm == "VEHICLE") or (cm == "AGENT")
    # Nota: AGENT no siempre implica radar en movimiento, pero para el cálculo interno
    # usamos MOBILE como escenario habitual de patrulla. Si se prefiere, cambiar a False.
    if measured <= 100:
        return 7.0 if mobile else 5.0
    # porcentaje
    pct = 0.07 if mobile else 0.05
    return round(measured * pct, 2)

def _dgt_speed_sanction_table() -> Dict[int, list]:
    """Tabla DGT (Sede electrónica) de sanciones por exceso de velocidad captado por cinemómetro.

    Devuelve por límite (20..120) una lista de bandas: (from,to,fine,points,label)

    Fuente visual: PDF DGT 'Sanciones por exceso de velocidad'.
    """
    # Bandas leídas de la tabla DGT (rangos inclusivos).
    return {

        20: [(21,40,100,0,'100€ sin puntos'), (41,50,300,2,'300€ 2 puntos'), (51,60,400,4,'400€ 4 puntos'), (61,70,500,6,'500€ 6 puntos'), (71,999,600,6,'600€ 6 puntos')],

        30: [(31,50,100,0,'100€ sin puntos'), (51,60,300,2,'300€ 2 puntos'), (61,70,400,4,'400€ 4 puntos'), (71,80,500,6,'500€ 6 puntos'), (81,999,600,6,'600€ 6 puntos')],

        40: [(41,60,100,0,'100€ sin puntos'), (61,70,300,2,'300€ 2 puntos'), (71,80,400,4,'400€ 4 puntos'), (81,90,500,6,'500€ 6 puntos'), (91,999,600,6,'600€ 6 puntos')],

        50: [(51,70,100,0,'100€ sin puntos'), (71,80,300,2,'300€ 2 puntos'), (81,90,400,4,'400€ 4 puntos'), (91,100,500,6,'500€ 6 puntos'), (121,999,600,6,'600€ 6 puntos')],

        60: [(61,90,100,0,'100€ sin puntos'), (91,110,300,2,'300€ 2 puntos'), (111,120,400,4,'400€ 4 puntos'), (121,130,500,6,'500€ 6 puntos'), (131,999,600,6,'600€ 6 puntos')],

        70: [(71,100,100,0,'100€ sin puntos'), (101,120,300,2,'300€ 2 puntos'), (121,130,400,4,'400€ 4 puntos'), (131,140,500,6,'500€ 6 puntos'), (141,999,600,6,'600€ 6 puntos')],

        80: [(81,110,100,0,'100€ sin puntos'), (111,130,300,2,'300€ 2 puntos'), (131,140,400,4,'400€ 4 puntos'), (141,150,500,6,'500€ 6 puntos'), (151,999,600,6,'600€ 6 puntos')],

        90: [(91,120,100,0,'100€ sin puntos'), (121,140,300,2,'300€ 2 puntos'), (141,150,400,4,'400€ 4 puntos'), (151,160,500,6,'500€ 6 puntos'), (161,999,600,6,'600€ 6 puntos')],

        100:[(101,130,100,0,'100€ sin puntos'), (131,150,300,2,'300€ 2 puntos'), (151,160,400,4,'400€ 4 puntos'), (161,170,500,6,'500€ 6 puntos'), (171,999,600,6,'600€ 6 puntos')],

        110:[(111,140,100,0,'100€ sin puntos'), (141,160,300,2,'300€ 2 puntos'), (161,170,400,4,'400€ 4 puntos'), (171,180,500,6,'500€ 6 puntos'), (181,999,600,6,'600€ 6 puntos')],

        120:[(121,150,100,0,'100€ sin puntos'), (151,170,300,2,'300€ 2 puntos'), (171,180,400,4,'400€ 4 puntos'), (181,190,500,6,'500€ 6 puntos'), (191,999,600,6,'600€ 6 puntos')],

    }


def _expected_speed_sanction(limit: int, corrected: float) -> Dict[str, Any]:
    tbl = _dgt_speed_sanction_table()
    lim = int(limit) if limit in tbl else None
    if lim is None:
        return {"fine": None, "points": None, "band": None, "table_limit": None}
    v = int(round(corrected))
    for lo, hi, fine, pts, label in tbl[lim]:
        if v >= lo and v <= hi:
            return {"fine": fine, "points": pts, "band": label, "table_limit": lim, "corrected_int": v}
    return {"fine": None, "points": None, "band": None, "table_limit": lim, "corrected_int": v}

def _compute_velocity_calc(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]], capture_mode: str) -> Dict[str, Any]:
    blob_parts = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False))
    except Exception:
        pass
    for d in docs or []:
        if d.get("text_excerpt"):
            blob_parts.append(d["text_excerpt"])
    blob = "\n".join(blob_parts)
    pair = _extract_speed_pair_from_blob(blob)
    measured = pair.get("measured")
    limit = pair.get("limit")
    if not measured or not limit:
        return {"ok": False, "reason": "No se pudieron extraer velocidades de forma fiable.", "raw": pair}

    margin = _speed_margin_value(int(measured), capture_mode)
    corrected = max(0.0, float(measured) - float(margin))
    expected = _expected_speed_sanction(int(limit), corrected)

    return {
        "ok": True,
        "limit": int(limit),
        "measured": int(measured),
        "capture_mode_for_margin": ("MOBILE" if (capture_mode or "").upper()=="AGENT" else (capture_mode or "UNKNOWN")),
        "margin_value": margin,
        "corrected": round(corrected, 2),
        "expected": expected,
        "extraction_confidence": pair.get("confidence", 0.0),
    }



def _llm_json(prompt: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def _save_event(case_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) "
                "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
            ),
            {"case_id": case_id, "type": event_type, "payload": json.dumps(payload)},
        )


def _load_latest_extraction(case_id: str) -> Optional[Dict[str, Any]]:
    """Devuelve el JSONB tal y como está guardado en extractions.extracted_json."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
            {"case_id": case_id},
        ).fetchone()
    return row[0] if row else None


def _load_interested_data(case_id: str) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COALESCE(interested_data,'{}'::jsonb) FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
    return (row[0] if row and row[0] else {}) or {}


def _load_case_flags(case_id: str) -> Dict[str, bool]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false) FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
    return {"test_mode": bool(row[0]) if row else False, "override_deadlines": bool(row[1]) if row else False}


def _load_case_documents(case_id: str) -> List[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT kind, b2_bucket, b2_key, mime, size_bytes, created_at "
                "FROM documents WHERE case_id=:case_id ORDER BY created_at ASC"
            ),
            {"case_id": case_id},
        ).fetchall()

    docs: List[Dict[str, Any]] = []
    for i, r in enumerate(rows, start=1):
        kind, bucket, key, mime, size_bytes, created_at = r
        text_excerpt = load_text_from_b2(bucket, key, mime)
        if text_excerpt:
            text_excerpt = text_excerpt[:MAX_EXCERPT_CHARS]

        docs.append(
            {
                "doc_index": i,
                "kind": kind,
                "bucket": bucket,
                "key": key,
                "mime": mime,
                "size_bytes": int(size_bytes or 0),
                "created_at": str(created_at),
                "text_excerpt": text_excerpt or "",
            }
        )
    return docs


def _detect_capture_mode(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]]) -> str:
    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False))
    except Exception:
        pass

    for d in docs or []:
        t = (d.get("text_excerpt") or "")
        if t:
            blob_parts.append(t)

    blob = "\n".join(blob_parts).lower()

    auto_signals = [
        "cámara", "camara", "fotograma", "fotogramas", "secuencia", "foto", "fotografía", "fotografia",
        "captación automática", "captacion automatica", "sistema automático", "sistema automatico",
        "dispositivo", "sensor", "instalación", "instalacion", "vídeo", "video"
    ]
    agent_signals = [
        "agente", "policía", "policia", "guardia civil", "denunciante", "observó", "observo",
        "manifestó", "manifesto", "presencial", "in situ"
    ]

    auto_score = sum(1 for s in auto_signals if s in blob)
    agent_score = sum(1 for s in agent_signals if s in blob)

    if ("motivo de no notificación" in blob or "motivo de no notificacion" in blob) and (
        "vehículo en marcha" in blob or "vehiculo en marcha" in blob
    ):
        pass

    if auto_score >= 2 and auto_score >= agent_score + 1:
        return "AUTO"
    if agent_score >= 2 and agent_score >= auto_score + 1:
        return "AGENT"
    return "UNKNOWN"


def _infer_infraction_from_facts_phrases(classify: Dict[str, Any]) -> Optional[str]:
    phrases = (classify or {}).get("facts_phrases") or []
    if not phrases:
        return None
    joined = "\n".join([str(p) for p in phrases if p]).lower()
    if any(s in joined for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
        return "semaforo"
    if any(s in joined for s in ["móvil", "movil", "teléfono", "telefono"]):
        return "movil"
    if any(s in joined for s in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro"]):
        return "velocidad"
    return None


def _has_semaforo_signals(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]], classify: Optional[Dict[str, Any]] = None) -> bool:
    phrases = (classify or {}).get("facts_phrases") or []
    for p in phrases:
        pl = (p or "").lower()
        if any(s in pl for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
            return True

    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False).lower())
    except Exception:
        pass
    for d in docs or []:
        blob_parts.append((d.get("text_excerpt") or "").lower())
    blob = "\n".join(blob_parts)

    signals = ["semáforo", "semaforo", "fase roja", "no respetar la luz roja", "circular con luz roja"]
    return any(s in blob for s in signals)


def _build_facts_summary(extraction_core: Optional[Dict[str, Any]], attack_plan: Dict[str, Any]) -> str:
    inf = ((attack_plan or {}).get("infraction_type") or "").lower()
    try:
        hecho = (extraction_core or {}).get("hecho_imputado")
        if isinstance(hecho, str) and hecho.strip():
            hl = hecho.lower()

            def consistent() -> bool:
                if inf == "semaforo":
                    return any(k in hl for k in ["semáforo", "semaforo", "fase roja", "rojo"])
                if inf == "velocidad":
                    return any(k in hl for k in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro"])
                if inf == "movil":
                    return any(k in hl for k in ["móvil", "movil", "teléfono", "telefono"])
                return True

            if consistent():
                return hecho.strip()
    except Exception:
        pass
    return ""


def _build_attack_plan(classify: Dict[str, Any], timeline: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    global_refs = (classify or {}).get("global_refs") or {}
    organism = (global_refs.get("main_organism") or "").lower()
    traffic = ("tráfico" in organism) or ("dgt" in organism)

    blob = json.dumps(extraction_core or {}, ensure_ascii=False).lower()
    inferred = _infer_infraction_from_facts_phrases(classify)

    triage_tipo = None
    try:
        triage_tipo = (extraction_core or {}).get("tipo_infraccion")
    except Exception:
        triage_tipo = None

    infraction_type = inferred or "generic"

    if infraction_type == "generic" and triage_tipo in ("semaforo", "velocidad", "movil", "atencion", "parking"):
        infraction_type = triage_tipo

    if infraction_type == "generic":
        if any(s in blob for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
            infraction_type = "semaforo"
        elif any(s in blob for s in ["teléfono", "telefono", "móvil", "movil"]):
            infraction_type = "movil"
        elif any(s in blob for s in ["km/h", "radar", "cinemómetro", "cinemometro", "velocidad"]):
            infraction_type = "velocidad"

    plan = {
        "infraction_type": infraction_type,
        "primary": {
            "title": "Insuficiencia probatoria específica",
            "points": [
                "La carga de la prueba corresponde a la Administración.",
                "No cabe sancionar sin prueba suficiente y concreta del hecho infractor.",
            ],
        },
        "secondary": [],
        "proof_requests": [],
        "petition": {
            "main": "Archivo / estimación íntegra",
            "subsidiary": "Subsidiariamente, práctica de prueba y aportación documental completa",
        },
    }

    if traffic:
        if infraction_type == "movil":
            plan["secondary"].append(
                {
                    "title": "Uso manual del móvil: prueba objetiva y motivación reforzada",
                    "points": [
                        "Debe acreditarse de forma concreta el uso manual (circunstancias y descripción suficiente).",
                        "Si no consta prueba objetiva o descripción detallada, procede el archivo por insuficiencia probatoria.",
                    ],
                }
            )
            plan["proof_requests"] += [
                "Boletín/denuncia/acta completa, con identificación del agente si consta.",
                "Descripción detallada del hecho y circunstancias (lugar/hora/forma de observación).",
                "Si existiera: fotografía/vídeo/capturas completas.",
            ]

        if infraction_type == "velocidad":
            plan["secondary"].append(
                {
                    "title": "Velocidad: prueba técnica completa (cinemómetro/radar)",
                    "points": [
                        "Debe constar identificación del cinemómetro y certificado vigente de verificación/calibración.",
                        "Debe constar margen aplicado y capturas completas.",
                    ],
                }
            )
            plan["proof_requests"] += [
                "Capturas/fotografías completas del hecho infractor.",
                "Identificación del cinemómetro (marca/modelo/nº serie) y ubicación exacta.",
                "Certificado de verificación/calibración vigente y constancia del margen aplicado.",
            ]

        tl = (timeline or {}).get("timeline") or []
        dates: List[str] = []
        for ev in tl:
            d = ev.get("date")
            if isinstance(d, str) and len(d) >= 10:
                dates.append(d[:10])
        if dates:
            oldest = sorted(dates)[0]
            if oldest.startswith("201") or oldest.startswith("200"):
                plan["secondary"].insert(
                    0,
                    {
                        "title": "Antigüedad del expediente: acreditación de notificación, firmeza y actos interruptivos",
                        "points": [
                            "Dada la antigüedad, corresponde acreditar notificación válida, firmeza y, en su caso, actos interruptivos.",
                            "Si no consta acreditación suficiente, procede el archivo.",
                        ],
                    },
                )
                plan["proof_requests"] += [
                    "Acreditación de la notificación válida (fecha de recepción/acuse/medio).",
                    "Acreditación de firmeza y actuaciones interruptivas, si existieran.",
                    "Estado actual del expediente y fundamento de su vigencia.",
                ]

    return plan


def _map_precept_to_type(extraction_core: Dict[str, Any]) -> Optional[str]:
    if not isinstance(extraction_core, dict):
        return None
    norma_hint = (extraction_core.get("norma_hint") or "").upper()
    precepts = extraction_core.get("preceptos_detectados") or []

    if "8/2004" in norma_hint or any("8/2004" in (p or "") for p in precepts) or any("LSOA" in (p or "").upper() for p in precepts):
        return "seguro"

    art = extraction_core.get("articulo_infringido_num")
    if isinstance(art, str) and art.isdigit():
        art = int(art)
    if isinstance(art, int):
        if art in (12, 15):
            return "condiciones_vehiculo"
        if art == 18:
            return "atencion"
        if art == 167:
            return "marcas_viales"

    if any("2822/98" in (p or "") for p in precepts) or "2822/98" in norma_hint:
        return "condiciones_vehiculo"

    blob = json.dumps(extraction_core, ensure_ascii=False).lower()
    if "9.1 bis" in blob or "9,1 bis" in blob:
        return "no_identificar"

    return None


def _apply_tipicity_guard(attack_plan: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(attack_plan or {})
    inferred = (plan.get("infraction_type") or "").lower().strip()
    mapped = (_map_precept_to_type(extraction_core) or "").lower().strip()

    if mapped and inferred in ("", "generic", "otro"):
        plan["infraction_type"] = mapped
        plan.setdefault("meta", {})
        plan["meta"]["precept_forced_type"] = mapped
        return plan

    if mapped and inferred and mapped != inferred:
        sec = plan.get("secondary") or []
        sec = list(sec) if isinstance(sec, list) else []
        sec.insert(
            0,
            {
                "title": "Principio de tipicidad: posible incongruencia entre el precepto citado y el hecho denunciado",
                "points": [
                    "La Administración debe subsumir el hecho descrito en el precepto concreto citado, con motivación suficiente.",
                    "Si el hecho denunciado no encaja en el artículo indicado, se vulnera el principio de tipicidad (Derecho sancionador) y procede el archivo.",
                    "Se solicita aclaración y acreditación completa del encaje típico, aportando el expediente íntegro y la base normativa aplicada.",
                ],
            },
        )
        plan["secondary"] = sec

        pr = plan.get("proof_requests") or []
        pr = list(pr) if isinstance(pr, list) else []
        pr += [
            "Copia íntegra del expediente administrativo (incluyendo propuesta/resolución y fundamentos).",
            "Identificación expresa del precepto aplicado (artículo/apartado) y su encaje con el hecho denunciado.",
            "Aportación de la norma/ordenanza aplicable y motivación completa.",
        ]
        seen = set()
        pr2 = []
        for x in pr:
            if x not in seen:
                seen.add(x)
                pr2.append(x)
        plan["proof_requests"] = pr2

        plan.setdefault("meta", {})
        plan["meta"]["tipicity_mismatch"] = {"mapped": mapped, "inferred": inferred}

    return plan


def _compute_context_intensity(timeline: Dict[str, Any], extraction_core: Dict[str, Any], classify: Dict[str, Any]) -> str:
    blob = ""
    try:
        blob = json.dumps(extraction_core or {}, ensure_ascii=False).lower()
    except Exception:
        blob = ""

    precepts = (extraction_core or {}).get("preceptos_detectados") or []
    pre_blob = " ".join([str(p) for p in precepts]).lower()

    has_lsoa = ("lsoa" in pre_blob) or ("8/2004" in pre_blob) or ("8/2004" in blob)
    has_speed = ("km/h" in blob) or ("cinemómetro" in blob) or ("cinemometro" in blob) or ("radar" in blob)
    if has_lsoa and has_speed:
        return "critico"

    dates: List[str] = []
    tl = (timeline or {}).get("timeline") or []
    for ev in tl:
        d = ev.get("date")
        if isinstance(d, str) and len(d) >= 10:
            dates.append(d[:10])
    for k in ("fecha_documento", "fecha_notificacion"):
        v = (extraction_core or {}).get(k)
        if isinstance(v, str) and len(v) >= 10:
            dates.append(v[:10])

    if dates:
        oldest = sorted(dates)[0]
        if oldest[:4].isdigit() and int(oldest[:4]) <= 2015:
            return "reforzado"

    return "normal"


def run_expediente_ai(case_id: str) -> Dict[str, Any]:
    docs = _load_case_documents(case_id)
    if not docs:
        raise RuntimeError("No hay documentos asociados al expediente.")

    extraction_wrapper = _load_latest_extraction(case_id) or {}
    extraction_core = (extraction_wrapper.get("extracted") or {}) if isinstance(extraction_wrapper, dict) else {}

    capture_mode = _detect_capture_mode(docs, extraction_core)

    classify = _llm_json(
        PROMPT_CLASSIFY,
        {"case_id": case_id, "documents": docs, "latest_extraction": extraction_wrapper},
    )

    timeline = _llm_json(
        PROMPT_TIMELINE,
        {"case_id": case_id, "classification": classify, "documents": docs, "latest_extraction": extraction_wrapper},
    )

    phase = _llm_json(
        PROMPT_PHASE,
        {"case_id": case_id, "classification": classify, "timeline": timeline, "latest_extraction": extraction_wrapper},
    )

    admissibility = _llm_json(
        PROMPT_GUARD,
        {
            "case_id": case_id,
            "recommended_action": phase,
            "timeline": timeline,
            "classification": classify,
            "latest_extraction": extraction_wrapper,
        },
    )

    flags = _load_case_flags(case_id)
    override_mode = _override_mode()
    if flags.get("test_mode") and flags.get("override_deadlines"):
        original_adm = admissibility.get("admissibility")
        admissibility["original_admissibility"] = original_adm
        admissibility["admissibility"] = "ADMISSIBLE"
        admissibility["can_generate_draft"] = True
        admissibility["deadline_status"] = admissibility.get("deadline_status") or "UNKNOWN"
        # En pruebas, forzamos un estado de plazo no bloqueante para que el generador no se autolimite.
        if override_mode == "SANDBOX_DEMO":
            admissibility["deadline_status"] = "OK"
        admissibility["required_constraints"] = admissibility.get("required_constraints") or []
        admissibility["override_applied"] = True
        admissibility["override_mode"] = override_mode
        _save_event(case_id, "test_override_applied", {"flags": flags, "override_mode": override_mode, "original_admissibility": original_adm})

    force_semaforo = _has_semaforo_signals(docs, extraction_core, classify)

    if force_semaforo:
        sem = module_semaforo()
        secondary_attacks = list(sem.get("secondary_attacks") or [])

        if capture_mode == "AUTO":
            secondary_attacks.insert(
                0,
                {
                    "title": "Captación automática: exigencia de secuencia completa y verificación del sistema",
                    "points": [
                        "Debe aportarse secuencia completa que permita verificar fase roja activa en el instante del cruce.",
                        "Debe acreditarse el correcto funcionamiento/sincronización del sistema de captación.",
                    ],
                },
            )
        elif capture_mode == "AGENT":
            secondary_attacks.insert(
                0,
                {
                    "title": "Denuncia presencial: motivación reforzada y descripción detallada de la observación",
                    "points": [
                        "Debe describirse con precisión la observación (ubicación, visibilidad, distancia y circunstancias).",
                        "La falta de detalle impide contradicción efectiva y genera indefensión.",
                    ],
                },
            )
        else:
            secondary_attacks.insert(
                0,
                {
                    "title": "Tipo de captación no concluyente: aportar prueba completa para evitar indefensión",
                    "points": [
                        "Debe aportarse la prueba completa del hecho: secuencia/fotogramas si captación automática, o descripción detallada si denuncia presencial.",
                        "En caso de no constar, procede el archivo por insuficiencia probatoria.",
                    ],
                },
            )

        attack_plan = {
            "infraction_type": "semaforo",
            "primary": {
                "title": (sem.get("primary_attack") or {}).get("title") or "Insuficiencia probatoria",
                "points": (sem.get("primary_attack") or {}).get("points") or [],
            },
            "secondary": [{"title": sa.get("title"), "points": sa.get("points") or []} for sa in secondary_attacks],
            "proof_requests": sem.get("proof_requests") or [],
            "petition": {
                "main": "Archivo / estimación íntegra",
                "subsidiary": "Subsidiariamente, práctica de prueba y aportación documental completa",
            },
            "meta": {"capture_mode": capture_mode, "forced": True},
        }
    else:
        attack_plan = _build_attack_plan(classify, timeline, extraction_core or {})

    attack_plan = _apply_tipicity_guard(attack_plan, extraction_core)
    facts_summary = _build_facts_summary(extraction_core, attack_plan)
    context_intensity = _compute_context_intensity(timeline, extraction_core, classify)

    # Normalización opcional para demos internas en modo SANDBOX_DEMO
    try:
        if (admissibility or {}).get("override_applied") and (admissibility or {}).get("override_mode") == "SANDBOX_DEMO":
            attack_plan = _sanitize_for_sandbox_demo(attack_plan)
            context_intensity = "normal"
    except Exception:
        pass

    # VELOCITY STRICT ENGINE (VSE-1): cálculo automático de margen, velocidad corregida y tramo sancionador
    velocity_calc = {}
    try:
        if (attack_plan or {}).get("infraction_type") == "velocidad":
            velocity_calc = _compute_velocity_calc(docs, extraction_core, capture_mode)
            if velocity_calc.get("ok"):
                attack_plan.setdefault("meta", {})
                attack_plan["meta"]["velocity_calc"] = velocity_calc
                # Si no consta acreditado margen/tabla o hay inconsistencia evidente, subimos intensidad
                context_intensity = "critico"
    except Exception:
        velocity_calc = {"ok": False, "reason": "error_interno_velocity_calc"}


    draft = None
    if bool(admissibility.get("can_generate_draft")) or (admissibility.get("admissibility") or "").upper() == "ADMISSIBLE":
        interested_data = _load_interested_data(case_id)
        draft = _llm_json(
            PROMPT_DRAFT,
            {
                "case_id": case_id,
                "interested_data": interested_data,
                "classification": classify,
                "timeline": timeline,
                "recommended_action": phase,
                "admissibility": admissibility,
                "latest_extraction": extraction_wrapper,
                "extraction_core": extraction_core,
                "attack_plan": attack_plan,
                "facts_summary": facts_summary,
                "context_intensity": context_intensity,
                "velocity_calc": velocity_calc,
                "sandbox": {"override_applied": bool((admissibility or {}).get("override_applied")), "override_mode": (admissibility or {}).get("override_mode")},
            },
        )


        # Post-procesado determinista VELOCIDAD (potencia + coherencia + archivo)
        try:
            if isinstance(draft, dict) and ((attack_plan or {}).get("infraction_type") == "velocidad"):
                cuerpo = draft.get("cuerpo") or ""
                cuerpo = _force_velocity_first_title(cuerpo)
                _force_velocity_asunto(draft)
                cuerpo = _ensure_speed_antecedentes(cuerpo, velocity_calc)
                cuerpo = _ensure_velocity_calc_paragraph(cuerpo, velocity_calc)
                cuerpo = _velocity_pro_enrich(cuerpo, velocity_calc)
                cuerpo = _remove_tipicity_intruder_in_speed(cuerpo)
                cuerpo = _force_archivo_in_speed_body(cuerpo)
                cuerpo = _fix_solicito_format(cuerpo)
                cuerpo = _normalize_velocity_titles_and_remove_tipicity(cuerpo, attack_plan)
                cuerpo = _fix_solicito_newline(cuerpo)
                draft["cuerpo"] = cuerpo
        except Exception as _e:
            _save_event(case_id, "postprocess_speed_failed", {"error": str(_e)})
        # Auto-repair (1 intento) para VELOCIDAD si el borrador no cumple mínimos VSE-1
        try:
            if isinstance(draft, dict):
                cuerpo0 = (draft.get("cuerpo") or "")
                missing = _velocity_strict_missing(cuerpo0) if cuerpo0 else []
                if missing and ((attack_plan or {}).get("infraction_type") == "velocidad"):
                    _save_event(case_id, "draft_repair_triggered", {"missing": missing})
                    draft = _llm_json(
                        PROMPT_DRAFT_REPAIR_VELOCIDAD,
                        {
                            "case_id": case_id,
                            "missing": missing,
                            "previous_draft": draft,
                            "attack_plan": attack_plan,
                            "facts_summary": facts_summary,
                            "context_intensity": context_intensity,
                            "velocity_calc": velocity_calc,
                            "latest_extraction": extraction_wrapper,
                            "classification": classify,
                            "timeline": timeline,
                            "admissibility": admissibility,
                        },
                    )

                    # Post-procesado tras REPAIR (velocidad)
                    try:
                        if isinstance(draft, dict) and ((attack_plan or {}).get("infraction_type") == "velocidad"):
                            cuerpo = draft.get("cuerpo") or ""
                            cuerpo = _ensure_speed_antecedentes(cuerpo, velocity_calc)
                            cuerpo = _ensure_velocity_calc_paragraph(cuerpo, velocity_calc)
                            cuerpo = _velocity_pro_enrich(cuerpo, velocity_calc)
                            cuerpo = _force_archivo_in_speed_body(cuerpo)
                            cuerpo = _normalize_velocity_titles_and_remove_tipicity(cuerpo, attack_plan)
                            cuerpo = _fix_solicito_newline(cuerpo)
                            draft["cuerpo"] = cuerpo
                    except Exception as _e:
                        _save_event(case_id, "postprocess_speed_failed", {"error": str(_e)})
        except Exception as _e:
            _save_event(case_id, "draft_repair_failed", {"error": str(_e)})


    result = {
        "ok": True,
        "case_id": case_id,
        "classify": classify,
        "timeline": timeline,
        "phase": phase,
        "admissibility": admissibility,
        "attack_plan": attack_plan,
        "draft": draft,
        "capture_mode": capture_mode,
        "facts_summary": facts_summary,
        "context_intensity": context_intensity,
        "velocity_calc": velocity_calc,
        "extraction_debug": {
            "wrapper_keys": list(extraction_wrapper.keys()) if isinstance(extraction_wrapper, dict) else [],
            "core_keys": list(extraction_core.keys()) if isinstance(extraction_core, dict) else [],
        },
    }

    _save_event(case_id, "ai_expediente_result", result)
    return result
