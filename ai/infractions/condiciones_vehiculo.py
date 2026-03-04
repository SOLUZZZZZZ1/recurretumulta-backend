
"""
RTM — CONDICIONES DEL VEHÍCULO (ART. 12 / 15 RGC – RGV 2822/98)
VERSIÓN DEMOLEDORA — ESPECÍFICA PARA DEFECTOS TÉCNICOS

Submódulos detectados automáticamente:
- Alumbrado / señalización óptica
- Neumáticos
- ITV
- Reformas / homologación

El texto se adapta al motivo real del boletín.
"""

from __future__ import annotations
from typing import Any, Dict


# ---------------------------------------------------------
# Detector de contexto
# ---------------------------------------------------------

def is_condiciones_vehiculo_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "condiciones_vehiculo":
        return True

    hecho = str(core.get("hecho_imputado") or "")
    raw = str(core.get("raw_text_blob") or "")
    blob = (body or "") + "\n" + hecho + "\n" + raw
    b = blob.lower()

    signals = [
        "alumbrado",
        "señalización óptica",
        "senalizacion optica",
        "anexo ii",
        "luz roja",
        "destellos",
        "neumático",
        "neumatico",
        "itv",
        "reforma",
        "homologación",
        "homologacion"
    ]

    return any(s in b for s in signals)


# ---------------------------------------------------------
# Plantilla principal
# ---------------------------------------------------------

def build_condiciones_vehiculo_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO."

    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    blob = ((body or "") + "\n" + hecho + "\n" + str(core.get("raw_text_blob") or "")).lower()

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    texto_base = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
    )

    # ---------------------------------------------------------
    # SUBTIPO: ALUMBRADO
    # ---------------------------------------------------------

    if any(k in blob for k in ["luz", "alumbrado", "destello", "señalización óptica"]):

        texto_base += (
            "ALEGACIÓN PRIMERA — IDENTIFICACIÓN TÉCNICA DEL DISPOSITIVO\n\n"
            "La denuncia afirma que el vehículo emitía luz en forma de destellos, pero no se identifica "
            "el dispositivo concreto ni el apartado específico del Anexo II del Reglamento General de Vehículos "
            "que supuestamente se estaría incumpliendo.\n\n"
            "El Anexo II regula múltiples requisitos técnicos relativos al alumbrado, por lo que la mera referencia "
            "genérica a dicho anexo no permite determinar con precisión el elemento reglamentario presuntamente vulnerado.\n\n"

            "ALEGACIÓN SEGUNDA — AUSENCIA DE COMPROBACIÓN TÉCNICA\n\n"
            "La apreciación visual de un agente respecto a la forma de emisión luminosa no constituye "
            "por sí sola una comprobación técnica suficiente para afirmar el incumplimiento de los requisitos "
            "reglamentarios del sistema de alumbrado.\n\n"
            "No consta en el expediente:\n"
            "• Identificación del dispositivo luminoso.\n"
            "• Verificación de su homologación.\n"
            "• Comprobación técnica del sistema instalado.\n"
            "• Referencia concreta al requisito técnico supuestamente incumplido.\n\n"
        )

    # ---------------------------------------------------------
    # SUBTIPO: NEUMÁTICOS
    # ---------------------------------------------------------

    if "neumático" in blob or "neumatico" in blob:

        texto_base += (
            "ALEGACIÓN ESPECÍFICA — ESTADO DE LOS NEUMÁTICOS\n\n"
            "La normativa exige acreditar el incumplimiento concreto del dibujo mínimo o "
            "de las condiciones reglamentarias del neumático.\n"
            "No consta medición objetiva del dibujo ni verificación técnica documentada.\n\n"
        )

    # ---------------------------------------------------------
    # SUBTIPO: ITV
    # ---------------------------------------------------------

    if "itv" in blob:

        texto_base += (
            "ALEGACIÓN ESPECÍFICA — INSPECCIÓN TÉCNICA\n\n"
            "La imputación relativa a la ITV requiere acreditar la situación administrativa "
            "del vehículo mediante consulta a los registros oficiales correspondientes.\n"
            "No consta en el expediente dicha verificación documental.\n\n"
        )

    # ---------------------------------------------------------
    # SUBTIPO: REFORMAS
    # ---------------------------------------------------------

    if "reforma" in blob or "homolog" in blob:

        texto_base += (
            "ALEGACIÓN ESPECÍFICA — REFORMAS DE VEHÍCULO\n\n"
            "La normativa exige identificar la reforma concreta y el requisito técnico "
            "presuntamente incumplido. La mera apreciación visual no permite concluir "
            "la inexistencia de homologación sin comprobación documental.\n\n"
        )

    texto_base += (
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro con soporte técnico verificable.\n"
    )

    return {
        "asunto": asunto,
        "cuerpo": texto_base.strip()
    }
