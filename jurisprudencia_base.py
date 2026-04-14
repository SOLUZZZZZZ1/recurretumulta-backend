JURISPRUDENCIA_BASE = {
    "presuncion_inocencia": {
        "texto": "La presunción de inocencia, consagrada en el artículo 24 de la Constitución, ha sido reiteradamente interpretada por el Tribunal Supremo en el sentido de exigir una actividad probatoria suficiente, clara y concluyente, no bastando meras presunciones o afirmaciones genéricas.",
        "referencia": "Tribunal Supremo"
    },
    "carga_prueba": {
        "texto": "Corresponde a la Administración la carga de acreditar de forma suficiente los hechos constitutivos de la infracción, conforme a la doctrina consolidada del Tribunal Supremo en materia sancionadora.",
        "referencia": "Tribunal Supremo"
    },
    "tipicidad": {
        "texto": "El principio de tipicidad exige una adecuada subsunción de los hechos en el tipo infractor, tal como ha reiterado el Tribunal Supremo, no siendo admisible una interpretación extensiva en perjuicio del administrado.",
        "referencia": "Tribunal Supremo"
    },
    "motivacion": {
        "texto": "La obligación de motivación de los actos administrativos exige una fundamentación suficiente y concreta, conforme a la doctrina del Tribunal Supremo.",
        "referencia": "Tribunal Supremo"
    },
    "velocidad": {
        "texto": "La validez de los medios técnicos de control de velocidad exige el cumplimiento estricto de los requisitos metrológicos y de control, tal como ha reiterado el Tribunal Supremo, requiriéndose una acreditación completa del correcto funcionamiento del dispositivo.",
        "referencia": "Tribunal Supremo + normativa metrológica"
    },
    "art18": {
        "texto": "La imputación de infracciones relativas al uso de dispositivos durante la conducción exige una descripción concreta, detallada y circunstanciada de los hechos, conforme a la doctrina del Tribunal Supremo sobre exigencia probatoria en el ámbito sancionador.",
        "referencia": "Tribunal Supremo"
    },
    "itv": {
        "texto": "La imposición de sanciones por incumplimientos relacionados con la inspección técnica de vehículos requiere una acreditación precisa de las fechas relevantes y de la efectiva circulación del vehículo, conforme a los principios de prueba exigidos por la doctrina del Tribunal Supremo.",
        "referencia": "Tribunal Supremo"
    },
    "tipicidad_incoherencia": {
        "texto": "La correcta aplicación del principio de tipicidad exige que los hechos imputados se correspondan de forma precisa con el precepto aplicado, tal como ha reiterado el Tribunal Supremo, no siendo admisible una subsunción forzada o incongruente.",
        "referencia": "Tribunal Supremo"
    },
    "defectos_formales": {
        "texto": "La ausencia de motivación suficiente o la falta de claridad en la descripción de los hechos puede generar indefensión material, lo que resulta contrario a la doctrina del Tribunal Supremo en materia sancionadora.",
        "referencia": "Tribunal Supremo"
    },
    "cadena_custodia": {
        "texto": "La validez de la prueba en el procedimiento sancionador exige garantizar la trazabilidad y la integridad de los datos, conforme a los principios de prueba y seguridad jurídica desarrollados por la jurisprudencia del Tribunal Supremo.",
        "referencia": "Tribunal Supremo"
    }
}


def obtener_bloques_juridicos(tipo_infraccion: str, incluir_motivacion: bool = True) -> str:
    tipo = (tipo_infraccion or "").strip().lower()
    bloques = []

    bloques.append(JURISPRUDENCIA_BASE["presuncion_inocencia"]["texto"])
    bloques.append(JURISPRUDENCIA_BASE["carga_prueba"]["texto"])

    if incluir_motivacion:
        bloques.append(JURISPRUDENCIA_BASE["motivacion"]["texto"])

    if tipo in {"velocidad", "radar", "cinemometro", "cinemómetro"}:
        bloques.append(JURISPRUDENCIA_BASE["velocidad"]["texto"])
        bloques.append(JURISPRUDENCIA_BASE["cadena_custodia"]["texto"])
    elif tipo in {"art18", "movil", "móvil", "auriculares", "telefono", "teléfono"}:
        bloques.append(JURISPRUDENCIA_BASE["art18"]["texto"])
    elif tipo in {"itv"}:
        bloques.append(JURISPRUDENCIA_BASE["itv"]["texto"])
    elif tipo in {"tipicidad", "incoherencia", "hecho_precepto", "hecho-precepto"}:
        bloques.append(JURISPRUDENCIA_BASE["tipicidad"]["texto"])
        bloques.append(JURISPRUDENCIA_BASE["tipicidad_incoherencia"]["texto"])
    else:
        bloques.append(JURISPRUDENCIA_BASE["tipicidad"]["texto"])
        bloques.append(JURISPRUDENCIA_BASE["defectos_formales"]["texto"])

    vistos = set()
    bloques_finales = []
    for bloque in bloques:
        if bloque not in vistos:
            bloques_finales.append(bloque)
            vistos.add(bloque)

    return "\n\n".join(bloques_finales)
