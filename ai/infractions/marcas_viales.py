
"""
RTM — MARCAS VIALES / LÍNEA CONTINUA / SEÑALIZACIÓN HORIZONTAL (SVL-MV-1) — DEMOLEDOR (Modo B por defecto)

Cobertura inicial:
- RGC art. 167 (marcas viales) — casos típicos:
  - "no respetar una marca longitudinal continua"
  - "línea continua" al adelantar o cambiar de carril
  - "marca longitudinal continua sin causa justificada"

Determinista, sin OpenAI.
Modo B (estándar): técnico + jurídicamente contundente.
Modo C (solo graves): cuando hay puntos o sanción alta.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
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
        s = s.replace("€","").replace(".","").replace(",","").strip()
        return int(s) if s.isdigit() else None
    except Exception:
        return None

def _is_grave(core: Dict[str, Any]) -> bool:
    core = core or {}
    fine = _get_int(core.get("sancion_importe_eur") or core.get("importe") or core.get("importe_total_multa"))
    pts = _get_int(core.get("puntos_detraccion") or core.get("puntos") or 0) or 0
    if fine is not None and fine >= 1000:
        return True
    if pts and pts > 0:
        return True
    g = str(core.get("gravedad") or "").lower().strip()
    return g in ("grave","muy grave","critico","crítico")

def is_marcas_viales_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "marcas_viales":
        return True
    art = core.get("articulo_infringido_num")
    try:
        art_i = int(art) if art is not None and str(art).strip().isdigit() else None
    except Exception:
        art_i = None
    if art_i == 167:
        return True
    blob = ((body or "") + "\n" + str(core.get("hecho_imputado") or "")).lower()
    signals = ["marca longitudinal continua", "línea continua", "linea continua", "señalización horizontal", "senalizacion horizontal", "marca vial"]
    return any(s in blob for s in signals)

def build_marcas_viales_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = re.sub(r"\s+"," ", str(core.get("hecho_imputado") or "NO RESPETAR MARCA VIAL (LÍNEA CONTINUA) — ART. 167 RGC.")).strip()
    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""
    modo_c = _is_grave(core)

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — ART. 167 RGC: DELIMITACIÓN DEL TRAMO, VISIBILIDAD Y PRUEBA VERIFICABLE\n\n"
        "Para sancionar por no respetar una marca longitudinal continua debe acreditarse, de forma concreta y verificable, "
        "el rebase/invasión efectiva de la marca en un tramo donde dicha marca estaba correctamente implantada, era visible "
        "y aplicable a la maniobra imputada.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Ubicación exacta (vía, p.k., sentido) y delimitación del tramo con marca continua.\n"
        "2) Configuración de la vía y señalización concurrente (intersecciones, accesos, cambios de rasante/curvas).\n"
        "3) Conducta concreta: rebase completo vs invasión parcial; duración y momento exacto.\n"
        "4) Visibilidad/estado de la marca (desgaste, obras, suciedad, iluminación).\n"
        "5) Medio de constatación y condiciones de observación (posición del agente, distancia, ángulo, tráfico).\n"
        "6) Soporte objetivo (vídeo/secuencia/croquis) para contradicción efectiva.\n\n"
        "BLOQUE ESPECÍFICO — ADELANTAMIENTO: ENCAJE TÍPICO Y DATOS DE LA MANIOBRA\n\n"
        "Si se imputa que el rebase ocurrió al adelantar, debe identificarse el vehículo adelantado, distancias, "
        "momento de inicio/fin y por qué la marca era continua y aplicable en ese punto. Sin esos datos, la imputación es estereotipada.\n\n"
        "ALEGACIÓN SEGUNDA — MOTIVACIÓN INDIVIDUALIZADA Y CARGA PROBATORIA\n\n"
        "La carga de la prueba corresponde a la Administración. Sin prueba suficiente y motivación individualizada del encaje típico, "
        "procede el ARCHIVO por insuficiencia probatoria.\n"
    )
    if modo_c:
        cuerpo += (
            "\nALEGACIÓN ADICIONAL (MODO C — GRAVEDAD): LEGALIDAD, TIPICIDAD Y PRESUNCIÓN DE INOCENCIA\n\n"
            "En ausencia de soporte objetivo y delimitación del tramo, se vulneran garantías esenciales (art. 25 CE y art. 24 CE), "
            "procediendo el archivo y, en su caso, la anulación por falta de motivación suficiente.\n"
        )
    cuerpo += (
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación objetiva del rebase/invasión de la marca.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa (denuncia íntegra, croquis, fotografías/vídeo, y motivación detallada del tramo y señalización).\n"
    )
    return {"asunto": asunto, "cuerpo": cuerpo.strip()}
