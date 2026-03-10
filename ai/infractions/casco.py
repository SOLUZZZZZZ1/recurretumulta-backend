def build_casco_strong_template(core):
    expediente = core.get("expediente_ref") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."

    cuerpo = f"""
A la atención del órgano competente,

I. ANTECEDENTES
1) Órgano: {organo}
2) Expediente: {expediente}
3) Hecho imputado: NO UTILIZAR CASCO DE PROTECCIÓN

II. ALEGACIONES

ALEGACIÓN PRIMERA — FALTA DE ACREDITACIÓN DEL HECHO

La denuncia se limita a afirmar que el conductor no llevaba el casco de protección debidamente colocado o abrochado,
sin que se aporten elementos objetivos que permitan verificar dicha afirmación.

No consta:
- fotografía
- grabación
- descripción detallada de la observación

ALEGACIÓN SEGUNDA — CONDICIONES DE OBSERVACIÓN

No se especifica:
- distancia desde la que se realizó la observación
- condiciones de visibilidad
- duración de la observación

III. SOLICITO

1) Que se tengan por formuladas las presentes alegaciones.
2) Que se acuerde el archivo del expediente por insuficiencia probatoria.
"""

    return {
        "asunto": "ESCRITO DE ALEGACIONES — USO DE CASCO",
        "cuerpo": cuerpo.strip(),
    }