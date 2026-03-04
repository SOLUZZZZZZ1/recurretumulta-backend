"""RTM — CONDICIONES DEL VEHÍCULO (ART. 12 / 15 RGC – RGV 2822/98) — DEMOLEDOR ESPECÍFICO

Compatibilidad:
- build_condiciones_vehiculo_strong_template(core) (nombre esperado por generate.py)
"""

from __future__ import annotations
from typing import Any, Dict


def _build_condiciones_vehiculo_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO."

    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    blob = ((body or "") + "\n" + hecho + "\n" + str(core.get("raw_text_blob") or "")).lower()

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
    )

    # ALUMBRADO / SEÑALIZACIÓN ÓPTICA
    if any(k in blob for k in ["alumbrado", "señalización óptica", "senalizacion optica", "luz roja", "destello", "destellos", "anexo ii", "anexo i"]):
        cuerpo += (
            "ALEGACIÓN PRIMERA — ALUMBRADO/SEÑALIZACIÓN ÓPTICA: IDENTIFICACIÓN TÉCNICA Y NORMA CONCRETA\n\n"
            "La denuncia afirma un defecto en alumbrado/señalización (p. ej., luz roja con destellos), pero no identifica el dispositivo concreto "
            "ni el apartado específico del Anexo aplicable del Reglamento General de Vehículos (RD 2822/98) que supuestamente se incumple.\n\n"
            "No basta una referencia genérica a 'Anexo' o a 'señalización óptica': debe precisarse el requisito técnico concreto y su encaje.\n\n"
            "ALEGACIÓN SEGUNDA — AUSENCIA DE COMPROBACIÓN TÉCNICA SUFICIENTE\n\n"
            "La mera apreciación visual no constituye por sí sola comprobación técnica bastante para afirmar incumplimiento reglamentario.\n\n"
            "No consta en el expediente:\n"
            "• Identificación técnica del dispositivo (tipo/ubicación/función).\n"
            "• Verificación de homologación/autorización.\n"
            "• Comprobación técnica documentada del sistema instalado.\n"
            "• Referencia al requisito técnico concreto supuestamente incumplido.\n\n"
        )

    # NEUMÁTICOS
    if ("neumático" in blob) or ("neumatico" in blob) or ("banda de rodadura" in blob) or ("dibujo" in blob):
        cuerpo += (
            "ALEGACIÓN ESPECÍFICA — NEUMÁTICOS: MEDICIÓN OBJETIVA\n\n"
            "Si se imputa defecto de neumáticos, debe acreditarse medición objetiva (profundidad de dibujo, estado) y circunstancia concreta. "
            "No consta medición técnica documentada ni soporte verificable.\n\n"
        )

    # ITV
    if "itv" in blob or "inspección técnica" in blob or "inspeccion tecnica" in blob:
        cuerpo += (
            "ALEGACIÓN ESPECÍFICA — ITV: VERIFICACIÓN DOCUMENTAL\n\n"
            "La imputación relativa a ITV requiere acreditación documental (consulta registral/estado administrativo) referida a la fecha del hecho. "
            "No consta en el expediente soporte verificable de dicha verificación.\n\n"
        )

    # REFORMAS / HOMOLOGACIÓN
    if "reforma" in blob or "homolog" in blob or "proyecto" in blob or "certificado" in blob:
        cuerpo += (
            "ALEGACIÓN ESPECÍFICA — REFORMAS/HOMOLOGACIÓN: IDENTIFICACIÓN CONCRETA\n\n"
            "Debe identificarse la reforma concreta y el requisito técnico presuntamente incumplido. "
            "La mera apreciación visual no permite concluir inexistencia de homologación sin comprobación documental.\n\n"
        )

    cuerpo += (
        "ALEGACIÓN FINAL — PRUEBA OBJETIVA Y MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "En Derecho sancionador, la carga de la prueba corresponde a la Administración. Sin identificación técnica suficiente, "
        "norma concreta aplicada y soporte verificable para contradicción, no puede tenerse por acreditado el hecho infractor.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica objetiva.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soporte técnico verificable (fotografías/vídeo/informe y precepto/anexo concreto aplicado).\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def build_condiciones_vehiculo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    """Función esperada por generate.py. NO CAMBIAR NOMBRE."""
    return _build_condiciones_vehiculo_template(core, body="")
