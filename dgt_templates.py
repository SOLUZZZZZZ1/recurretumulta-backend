from typing import Any, Dict

def _val(d: Dict[str, Any], key: str, default=None):
    return d.get(key, default)

def build_dgt_reposicion_text(extracted: Dict[str, Any], interesado: Dict[str, Any]) -> Dict[str, str]:
    expediente = _val(extracted, "expediente_ref") or "[EXPEDIENTE]"
    fecha_notif = _val(extracted, "fecha_notificacion") or "[FECHA NOTIFICACIÓN]"
    tipo = _val(extracted, "tipo_sancion") or "Sanción de tráfico"

    nombre = interesado.get("nombre", "[NOMBRE Y APELLIDOS]")
    dni = interesado.get("dni", "[DNI/NIE]")
    domicilio = interesado.get("domicilio", "[DOMICILIO]")
    localidad = interesado.get("localidad", "[LOCALIDAD]")

    asunto = f"Recurso de reposición · Expediente {expediente}"
    cuerpo = f"""A LA JEFATURA PROVINCIAL DE TRÁFICO QUE CORRESPONDA

D./Dª {nombre}, con DNI/NIE {dni}, con domicilio a efectos de notificaciones en {domicilio}, comparece y como mejor proceda,

EXPONE

1. Que con fecha {fecha_notif} le ha sido notificada resolución sancionadora dictada en el expediente sancionador nº {expediente}, relativo a: {tipo}.
2. Que no está conforme con la resolución notificada, y dentro de plazo interpone RECURSO DE REPOSICIÓN con carácter potestativo.

FUNDAMENTOS

I. Competencia y procedimiento: Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común; y Real Decreto Legislativo 6/2015 (Ley de Tráfico).
II. Falta de motivación / insuficiencia probatoria / error en los hechos (según corresponda), solicitando la revisión íntegra del expediente.

SOLICITA

Que, teniendo por presentado este escrito, se admita y, previos los trámites oportunos, se estime el presente recurso, dejando sin efecto la resolución sancionadora y archivando el expediente {expediente}.

En {localidad}, a ___ de __________ de 20__.

Fdo.: {nombre}
"""
    return {"asunto": asunto, "cuerpo": cuerpo}

def build_dgt_alegaciones_text(extracted: Dict[str, Any], interesado: Dict[str, Any]) -> Dict[str, str]:
    expediente = _val(extracted, "expediente_ref") or "[EXPEDIENTE]"
    fecha_notif = _val(extracted, "fecha_notificacion") or "[FECHA NOTIFICACIÓN]"
    tipo = _val(extracted, "tipo_sancion") or "Denuncia de tráfico"

    nombre = interesado.get("nombre", "[NOMBRE Y APELLIDOS]")
    dni = interesado.get("dni", "[DNI/NIE]")
    domicilio = interesado.get("domicilio", "[DOMICILIO]")
    localidad = interesado.get("localidad", "[LOCALIDAD]")

    asunto = f"Alegaciones · Expediente {expediente}"
    cuerpo = f"""A LA JEFATURA PROVINCIAL DE TRÁFICO QUE CORRESPONDA

D./Dª {nombre}, con DNI/NIE {dni}, con domicilio a efectos de notificaciones en {domicilio}, comparece y como mejor proceda,

EXPONE

1. Que con fecha {fecha_notif} le ha sido notificada denuncia en relación con el expediente sancionador nº {expediente}, relativo a: {tipo}.
2. Que dentro del plazo legal formula ALEGACIONES y propone/practica la prueba que corresponda.

ALEGACIONES

Primera.— (Indique el motivo principal: error en hechos, señalización, identificación, prueba, etc.)
Segunda.— (Añadir lo que corresponda al caso concreto.)

SOLICITA

Que, teniendo por presentadas estas alegaciones, se admitan y, previos los trámites oportunos, se acuerde el archivo del expediente {expediente} o, subsidiariamente, se practiquen las pruebas propuestas.

En {localidad}, a ___ de __________ de 20__.

Fdo.: {nombre}
"""
    return {"asunto": asunto, "cuerpo": cuerpo}
