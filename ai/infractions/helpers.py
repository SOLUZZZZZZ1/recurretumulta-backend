import re


ADMIN_FIELDS = [
    "importe multa",
    "importe con reducción",
    "fecha límite",
    "lugar de denuncia",
    "puntos a detraer",
    "matricula",
    "marca",
    "modelo",
    "clase vehículo",
    "expediente",
    "hora",
    "vía",
    "punto km",
    "sentido"
]


def normalize(text: str) -> str:
    t = text.lower()
    t = t.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    t = t.replace("vehículo", "vehiculo")
    t = t.replace("arcén", "arcen")
    return t


def is_admin_line(line: str) -> bool:
    l = normalize(line)
    return any(x in l for x in ADMIN_FIELDS)


def extract_hecho_literal(text: str) -> str:

    if not text:
        return ""

    text = text.replace("\r", "\n")

    m = re.search(r"hecho denunciado", normalize(text))
    if not m:
        return ""

    tail = text[m.end():]

    lines = [x.strip() for x in tail.split("\n") if x.strip()]

    collected = []
    started = False

    for line in lines:

        if is_admin_line(line):
            if started:
                break
            continue

        low = normalize(line)

        # códigos típicos 5A / 5B / 5C
        if re.match(r"5[a-z]", low):
            started = True
            line = re.sub(r"5[a-z]\s*", "", line)
            collected.append(line)
            continue

        # línea narrativa
        if not started:
            if any(k in low for k in [
                "conducir",
                "circular",
                "no respetar",
                "utilizando",
                "bailando",
                "tocando",
                "golpeando",
                "auricular",
                "fase roja",
                "marca longitudinal",
                "sin mantener la atencion"
            ]):
                started = True
                collected.append(line)
            continue

        collected.append(line)

    if not collected:
        return ""

    literal = " ".join(collected)

    literal = re.sub(r"\s+", " ", literal)

    return literal.strip()