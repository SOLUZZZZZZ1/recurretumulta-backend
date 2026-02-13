# ai/prompts/module_semaforo.py
from typing import Dict, Any

def module_semaforo() -> Dict[str, Any]:
    return {
        "infraction_type": "semaforo",
        "primary_attack": {
            "title": "Insuficiencia probatoria en infracción por semáforo en fase roja",
            "points": [
                "La sanción por circular con luz roja exige prueba suficiente y concreta del hecho infractor.",
                "No basta una fórmula genérica; debe acreditarse la fase roja efectiva en el momento exacto del cruce.",
                "Debe constar identificación clara del vehículo y su posición respecto de la línea de detención."
            ]
        },
        "secondary_attacks": [
            {
                "title": "Acreditación técnica del sistema (si captación automática)",
                "points": [
                    "Si la captación es automática, debe acreditarse el correcto funcionamiento del sistema.",
                    "Debe constar secuencia completa de fotogramas/fotografías que permita verificar fase roja activa.",
                    "Debe acreditarse sincronización y parámetros mínimos (fase roja efectiva) si procede."
                ]
            },
            {
                "title": "Motivación reforzada si denuncia presencial",
                "points": [
                    "Si la denuncia es de agente, debe describirse con precisión la observación realizada.",
                    "Debe constar ubicación del agente, visibilidad, distancia y circunstancias del cruce.",
                    "La ausencia de descripción detallada impide contradicción y genera indefensión."
                ]
            }
        ],
        "proof_requests": [
            "Copia íntegra y legible del boletín/acta de denuncia.",
            "Secuencia completa de fotografías/fotogramas si se trata de sistema automático.",
            "Acreditación del correcto funcionamiento del sistema de captación (si existe).",
            "Detalle de la fase semafórica en el momento de la infracción y hora exacta.",
            "Identificación del agente y circunstancias de observación (si es denuncia presencial)."
        ]
    }
