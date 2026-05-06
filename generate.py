# generate.py FIX FINAL

from typing import Dict, Any

def force_semaforo(core: Dict[str, Any]) -> bool:
    blob = str(core).lower()
    return any(x in blob for x in [
        "luz roja", "fase roja", "semaforo", "semáforo",
        "llum vermella", "semàfor", "semafor"
    ])

def get_hecho(core):
    if force_semaforo(core):
        return "No respetar la luz roja no intermitente de un semáforo"
    return core.get("hecho", "")

def clean_duplicates(text: str) -> str:
    seen = set()
    lines = []
    for l in text.splitlines():
        if l.strip() and l not in seen:
            seen.add(l)
            lines.append(l)
    return "\n".join(lines)

def generate(core: Dict[str, Any], interesado: Dict[str, Any]):
    hecho = get_hecho(core)

    body = f'''
ESCRITO DE ALEGACIONES

EXPEDIENTE: {core.get("expediente_ref","")}
MATRÍCULA: {interesado.get("matricula","")}

HECHO:
{hecho}

ALEGACIONES:
- No consta prueba suficiente
- No consta fase roja acreditada
- No consta secuencia completa

SOLICITO ARCHIVO DEL EXPEDIENTE
'''

    body = clean_duplicates(body)

    return {
        "docx_kind": "recurso_docx",
        "pdf_kind": "recurso_pdf",
        "body": body
    }
