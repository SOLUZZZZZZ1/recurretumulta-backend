import re
from typing import Any, Dict, List


ADMIN_FIELDS = [
    "importe multa",
    "importe con reduccion",
    "importe con reducción",
    "fecha limite",
    "fecha límite",
    "lugar de denuncia",
    "puntos a detraer",
    "matricula",
    "marca y modelo",
    "marca",
    "modelo",
    "clase vehiculo",
    "clase vehículo",
    "datos del vehic",
    "domicilio",
    "provincia",
    "codigo postal",
    "código postal",
    "identificacion de la multa",
    "identificación de la multa",
    "organo",
    "órgano",
    "expediente",
    "fecha documento",
    "hora",
    "via ",
    "vía ",
    "punto km",
    "sentido",
    "titular",
    "boletin",
    "boletín",
    "agente denunciante",
    "observaciones internas",
    "jefatura",
]


def normalize_text(text: str) -> str:
    t = (text or "").lower()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
        "ñ": "n",
    }
    for k, v in replacements.items():
        t = t.replace(k, v)

    t = t.replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def clean_literal_text(text: str) -> str:
    t = (text or "").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    t = t.strip()

    t = re.sub(r"^\s*hecho denunciado\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*hecho imputado\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)

    # códigos típicos DGT/policía
    t = re.sub(r"^\s*[\(\[]?\s*5[abc]\s*[\)\]]?\s*", "", t, flags=re.IGNORECASE)

    # limpieza visual
    t = re.sub(r"\s+/\s+", " / ", t)
    t = re.sub(r"\s+", " ", t).strip(" :-\t")

    return t


def is_probably_admin_line(line: str) -> bool:
    l = normalize_text(line)
    return any(x in l for x in ADMIN_FIELDS)


def looks_like_narrative_line(line: str) -> bool:
    l = normalize_text(line)
    signals = [
        "conducir",
        "circular",
        "circulando",
        "circulaba",
        "no respetar",
        "no respeta",
        "utilizando",
        "bailando",
        "tocando",
        "golpeando",
        "auricular",
        "auriculares",
        "cascos",
        "luz roja",
        "fase roja",
        "marca longitudinal",
        "adelantamiento",
        "sin mantener",
        "atencion permanente",
        "atencion",
        "vehiculo resenado",
        "vehiculo reseñado",
        "observado por agente",
        "interceptado",
        "menor de",
        "ciclistas",
        "arcen",
        "en paralelo",
        "conversando",
        "telefono movil",
        "telefono",
        "movil",
        "itv",
        "seguro obligatorio",
        "alumbrado",
        "senalizacion optica",
        "linea continua",
        "linea de detencion",
        "uso manual",
        "radar",
        "cinemometro",
        "velocidad",
        "km/h",
    ]
    return any(k in l for k in signals)


def extract_literal_from_blob(raw_text: str) -> str:
    """
    Extrae el relato del agente desde texto OCR/PDF.
    Diseñado para formatos DGT / policía local con:
      HECHO DENUNCIADO
      5A ...
      5B ...
      5C ...
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return ""

    original_text = raw_text.replace("\r", "\n")
    normalized_text = normalize_text(original_text)

    m = re.search(r"hecho denunciado\s*[:\-]?\s*", normalized_text, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"hecho imputado\s*[:\-]?\s*", normalized_text, flags=re.IGNORECASE)
    if not m:
        return ""

    # localizar inicio aproximado en el texto original
    start = m.end()
    tail = original_text[start:].strip()
    if not tail:
        return ""

    lines = [ln.strip() for ln in tail.split("\n") if ln.strip()]
    if not lines:
        return ""

    collected: List[str] = []
    started = False

    for ln in lines:
        low = normalize_text(ln)

        if is_probably_admin_line(ln):
            if started:
                break
            continue

        # Códigos típicos 5A / 5B / 5C al principio del relato
        if re.match(r"^\s*5[abc]\b", low):
            started = True
            cleaned = re.sub(r"^\s*5[abc]\s*", "", ln, flags=re.IGNORECASE).strip()
            if cleaned:
                collected.append(cleaned)
            continue

        # Si aún no empezó, arrancar con una línea narrativa fuerte
        if not started:
            if looks_like_narrative_line(ln):
                started = True
                collected.append(ln)
            continue

        # Ya empezado: seguir recogiendo hasta chocar con bloque admin
        collected.append(ln)

        # corte de seguridad
        if len(" ".join(collected)) > 700:
            break

    if not collected:
        return ""

    out = " / ".join(collected)
    out = clean_literal_text(out)

    # reintento si quedó demasiado corto
    if len(out) < 40:
        second_pass: List[str] = []
        for ln in lines:
            if is_probably_admin_line(ln):
                if second_pass:
                    break
                continue
            second_pass.append(ln)
            if len(" ".join(second_pass)) > 700:
                break

        out2 = clean_literal_text(" / ".join(second_pass))
        if len(out2) > len(out):
            out = out2

    if len(out) > 550:
        out = out[:550].rsplit(" ", 1)[0].strip() + "…"

    return out.strip()


def extract_hecho_literal(core: Dict[str, Any]) -> str:
    """
    Prioridad:
    1) hecho_denunciado_literal si está bien informado
    2) raw_text_pdf / raw_text_vision / raw_text_blob
    3) fallback a hecho_imputado
    """
    core = core or {}

    val = core.get("hecho_denunciado_literal")
    if isinstance(val, str) and val.strip():
        cleaned = clean_literal_text(val)
        if len(cleaned) >= 25:
            return cleaned

    for key in ("raw_text_pdf", "raw_text_vision", "raw_text_blob"):
        val = core.get(key)
        if isinstance(val, str) and val.strip():
            extracted = extract_literal_from_blob(val)
            if extracted and len(extracted) >= 25:
                return extracted

    hecho = core.get("hecho_imputado")
    if isinstance(hecho, str) and hecho.strip():
        cleaned = clean_literal_text(hecho)
        if cleaned:
            return cleaned

    return ""


def detect_weak_signals(literal: str) -> List[str]:
    """
    Detecta puntos débiles o palancas de ataque dentro del relato.
    """
    l = normalize_text(literal)
    signals: List[str] = []

    if re.search(r"\b\d+(?:[.,]\d+)?\s*km\b", l):
        signals.append("distancia_observacion")

    if re.search(r"\b\d+\s+metros?\b", l):
        signals.append("metros_mencionados")

    if "observado" in l or "observada" in l or "observado por agente" in l:
        signals.append("condiciones_observacion")

    if "arcen" in l:
        signals.append("configuracion_via")

    if "ciclistas" in l or "bicicleta" in l or "bicicletas" in l:
        signals.append("circulacion_ciclistas")

    if "de a tres" in l or "en paralelo" in l:
        signals.append("ocupacion_lateral")

    if "menor de" in l or "bebe" in l or "bebe" in l or "asiento trasero" in l:
        signals.append("menor_en_vehiculo")

    if "auricular" in l or "auriculares" in l or "cascos" in l:
        signals.append("uso_auricular")

    if "telefono" in l or "movil" in l:
        signals.append("uso_movil")

    if "fase roja" in l or "luz roja" in l or "linea de detencion" in l:
        signals.append("semaforo")

    if "linea continua" in l or "marca longitudinal" in l:
        signals.append("marca_vial")

    if "adelantamiento" in l:
        signals.append("adelantamiento")

    if "radar" in l or "cinemometro" in l or "km/h" in l:
        signals.append("velocidad")

    return list(dict.fromkeys(signals))


def build_extra_attack_paragraphs(literal: str, tipo_infraccion: str = "") -> List[str]:
    """
    Convierte señales detectadas en párrafos de ataque reutilizables.
    """
    tipo = normalize_text(tipo_infraccion)
    signals = detect_weak_signals(literal)
    paragraphs: List[str] = []

    if "distancia_observacion" in signals and tipo in ("atencion", "negligente", "conduccion negligente", ""):
        paragraphs.append(
            "La denuncia afirma que la conducta fue observada durante un tramo determinado antes de proceder a la interceptación del vehículo. "
            "No se precisa cómo fue determinada dicha distancia ni cuál fue el punto exacto de inicio de la supuesta observación, extremos necesarios para valorar la fiabilidad del relato. "
            "Si la conducta generaba realmente un riesgo inmediato para la seguridad vial, resultaría lógico que la intervención se hubiera producido de forma inmediata."
        )

    if "configuracion_via" in signals:
        paragraphs.append(
            "El propio boletín menciona elementos de configuración de la vía que pueden resultar compatibles con una circulación sin riesgo efectivo, "
            "por lo que la imputación exige una descripción más precisa de la anchura útil, posición real de los usuarios y circunstancias concretas del tráfico para sostener la existencia de peligro objetivable."
        )

    if "circulacion_ciclistas" in signals:
        paragraphs.append(
            "Tratándose de circulación de ciclistas, no basta una referencia genérica a su disposición o conversación; "
            "debe concretarse la distancia entre ellos, la ocupación real de la vía, la intensidad del tráfico existente y la reacción concreta de terceros usuarios, "
            "sin lo cual no puede afirmarse con rigor una situación de riesgo real."
        )

    if "ocupacion_lateral" in signals:
        paragraphs.append(
            "La mera afirmación de que los usuarios circulaban 'de a tres' o 'en paralelo' no basta por sí sola para integrar una situación sancionable, "
            "si no se describe con precisión la anchura disponible, la trayectoria concreta y el peligro efectivo generado para la circulación."
        )

    if "menor_en_vehiculo" in signals and tipo in ("atencion", "negligente", "conduccion negligente", ""):
        paragraphs.append(
            "La referencia a la presencia de un menor en el vehículo no equivale por sí misma a la existencia de un riesgo real para la seguridad vial. "
            "Debe precisarse en qué momento fue observado, en qué condiciones y si existía o no sistema de retención infantil homologado."
        )

    if "condiciones_observacion" in signals and tipo in ("atencion", "movil", "auriculares", ""):
        paragraphs.append(
            "La denuncia se apoya en una observación visual cuya fiabilidad exige mayor concreción: posición exacta del agente, distancia, ángulo, tiempo de observación y condiciones de visibilidad. "
            "Sin esos datos, la apreciación realizada carece de la precisión necesaria para enervar la presunción de inocencia."
        )

    if "semaforo" in signals:
        paragraphs.append(
            "En materia de semáforo, la Administración debe acreditar de forma objetiva la fase roja activa en el instante exacto del supuesto rebase, "
            "la posición del vehículo respecto de la línea de detención y, en su caso, la secuencia completa o soporte verificable que permita reconstruir el hecho."
        )

    if "marca_vial" in signals and tipo in ("marcas_viales", "carril", ""):
        paragraphs.append(
            "La imputación relativa a marca vial exige identificar con precisión el tramo afectado, la visibilidad y estado de la señalización horizontal y la maniobra concreta atribuida, "
            "sin que baste una fórmula estereotipada sobre su mera existencia."
        )

    if "adelantamiento" in signals and tipo in ("marcas_viales", "carril", ""):
        paragraphs.append(
            "Si se atribuye la maniobra en contexto de adelantamiento, debe precisarse qué vehículo fue adelantado, "
            "cómo se desarrolló la maniobra y por qué la señalización era aplicable exactamente en ese punto y en ese momento."
        )

    return paragraphs