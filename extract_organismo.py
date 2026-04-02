import re

def extract_organismo_from_cuerpo(cuerpo: str) -> str:
    """
    Busca líneas tipo:
    A LA JEFATURA PROVINCIAL DE TRÁFICO DE ...
    AL AYUNTAMIENTO DE ...
    """

    if not cuerpo:
        return ""

    lines = cuerpo.split("\n")

    for line in lines:
        clean = line.strip().upper()

        if clean.startswith("A LA ") or clean.startswith("AL "):
            return clean

    return ""