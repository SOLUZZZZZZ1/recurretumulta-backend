import re

def extract_destination_from_text(text: str) -> str:
    """
    Busca líneas tipo:
    A LA JEFATURA PROVINCIAL DE TRÁFICO DE X
    AL AYUNTAMIENTO DE X
    A LA DIRECCIÓN GENERAL DE TRÁFICO
    """

    if not text:
        return ""

    lines = text.split("\n")

    for line in lines:
        clean = line.strip().upper()

        # patrones típicos administrativos
        if clean.startswith("A LA ") or clean.startswith("AL "):
            return clean

    return ""