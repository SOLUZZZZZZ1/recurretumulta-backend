def build_tacografo_strong_template(core):
    expediente = core.get("expediente_ref") or "[EXPEDIENTE]"
    organo = core.get("organismo") or "No consta acreditado."

    hecho = "Infracción relacionada con el uso del tacógrafo o tiempos de conducción y descanso"

    cuerpo = f"""
A la atención del órgano competente,

I. ANTECEDENTES

1) Órgano: {organo}
2) Identificación expediente: {expediente}
3) Hecho imputado: {hecho}

II. ALEGACIONES

ALEGACIÓN PRIMERA — INSUFICIENCIA PROBATORIA Y FALTA DE SOPORTE TÉCNICO

La imputación relativa al uso del tacógrafo o al cumplimiento de los tiempos de conducción y descanso exige una acreditación técnica precisa, completa y verificable.

No basta con una referencia genérica a un supuesto incumplimiento, sino que debe constar:

• Registro completo del tacógrafo en el periodo afectado  
• Identificación del conductor y del vehículo  
• Datos cronológicos exactos de conducción y descanso  
• Acreditación del dispositivo utilizado y su correcto funcionamiento  

La ausencia de estos elementos impide verificar con certeza la infracción imputada.

ALEGACIÓN SEGUNDA — FALTA DE TRAZABILIDAD Y CONTROL DE LOS DATOS

Los datos del tacógrafo deben ser íntegros, continuos y verificables.

No consta:

• Cadena de custodia de los datos  
• Integridad del registro digital  
• Ausencia de errores o manipulaciones en la lectura  

Sin trazabilidad completa, la prueba carece de fiabilidad suficiente.

ALEGACIÓN TERCERA — VULNERACIÓN DE LA PRESUNCIÓN DE INOCENCIA

La carga probatoria corresponde a la Administración.

En ausencia de prueba técnica suficiente y verificable, no puede entenderse desvirtuada la presunción de inocencia del interesado.

FUNDAMENTOS DE DERECHO

PRIMERO.– Artículos 24 y 25 de la Constitución Española (presunción de inocencia y legalidad sancionadora).

SEGUNDO.– Reglamento (CE) 561/2006 sobre tiempos de conducción y descanso.

TERCERO.– Reglamento (UE) 165/2014 relativo al tacógrafo.

CUARTO.– Ley 39/2015 de Procedimiento Administrativo Común.

QUINTO.– Jurisprudencia consolidada sobre exigencia de prueba técnica suficiente.

S U P L I C A:

1) Que se tengan por formuladas las presentes alegaciones.

2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.

3) Subsidiariamente, que se aporte expediente completo y datos íntegros del tacógrafo.

OTROSÍ DIGO

Que se reserva el ejercicio de acciones legales adicionales.
"""

    return {
        "asunto": "ESCRITO DE ALEGACIONES — TACÓGRAFO",
        "cuerpo": cuerpo
    }