from __future__ import annotations
from typing import Any, Dict


def build_cinturon_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "NO UTILIZAR CINTURÓN DE SEGURIDAD"

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — FALTA DE ACREDITACIÓN SUFICIENTE DEL HECHO\n\n"
        "La denuncia afirma que el conductor no hacía uso del cinturón de seguridad "
        "o no lo llevaba correctamente abrochado, pero no consta soporte objetivo "
        "que permita verificar dicha afirmación con el rigor exigible en Derecho sancionador.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Posición exacta del agente respecto del vehículo.\n"
        "2) Distancia desde la que se realizó la observación.\n"
        "3) Tiempo durante el cual se mantuvo la observación.\n"
        "4) Condiciones de visibilidad existentes en ese momento.\n"
        "5) Prueba objetiva adicional (fotografía, vídeo o secuencia).\n\n"
        "ALEGACIÓN SEGUNDA — NECESIDAD DE DESCRIPCIÓN CIRCUNSTANCIADA\n\n"
        "No basta una afirmación estereotipada sobre la falta de uso del cinturón. "
        "Debe precisarse si se observó claramente la ausencia total del cinturón, "
        "su colocación incorrecta o un eventual desabrochado momentáneo, así como "
        "las circunstancias concretas de la observación.\n\n"
        "ALEGACIÓN TERCERA — EXPEDIENTE ÍNTEGRO Y PRUEBA COMPLETA\n\n"
        "Se solicita la aportación íntegra del expediente administrativo, incluyendo "
        "la denuncia completa, la motivación individualizada y cualquier soporte probatorio "
        "que permita contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}