from pathlib import Path
import re

TARGET = Path(r"C:\recurretumulta\backend\generate.py")

def add_once(block: str, items: list[str]) -> str:
    for item in items:
        needle = f'"{item}"'
        if needle not in block:
            insert_at = block.rfind("]")
            if insert_at != -1:
                prefix = "" if block[:insert_at].rstrip().endswith("[") else ",\n            "
                block = block[:insert_at] + f'{prefix}"{item}"' + block[insert_at:]
    return block

def patch_add_list(text: str, tipo: str, new_items: list[str], points: int) -> str:
    pattern = rf'add\(\s*"{re.escape(tipo)}",\s*\[(.*?)\]\s*,\s*{points}\s*,?\s*\)'
    m = re.search(pattern, text, flags=re.S)
    if not m:
        print(f"No encontré bloque add('{tipo}')")
        return text
    original = m.group(0)
    inner = m.group(1)
    patched_inner = add_once("[" + inner + "]", new_items)[1:-1]
    patched = original.replace(inner, patched_inner, 1)
    return text.replace(original, patched, 1)

def patch_if_any_block(text: str, anchor: str, new_items: list[str]) -> str:
    idx = text.find(anchor)
    if idx == -1:
        print(f"No encontré anchor: {anchor[:50]}")
        return text
    start = text.find("[", idx)
    end = text.find("])", start)
    if start == -1 or end == -1:
        print(f"No pude localizar lista para anchor: {anchor[:50]}")
        return text
    block = text[start:end+1]
    patched = add_once(block, new_items)
    return text[:start] + patched + text[end+1:]

def main():
    if not TARGET.exists():
        print(f"No existe: {TARGET}")
        return

    text = TARGET.read_text(encoding="utf-8", errors="ignore")

    text = patch_add_list(text, "itv", [
        "inspeccion tecnica del vehiculo caducada",
        "inspección técnica del vehículo caducada",
        "carecer de inspeccion tecnica actualizada",
        "carecer de inspección técnica actualizada",
        "no tener vigente la inspeccion tecnica obligatoria",
        "no tener vigente la inspección técnica obligatoria",
        "itv expirada",
        "itv vencida",
    ], 3)

    text = patch_add_list(text, "seguro", [
        "cobertura minima obligatoria",
        "cobertura mínima obligatoria",
        "sin la cobertura minima obligatoria exigida",
        "sin la cobertura mínima obligatoria exigida",
        "poliza obligatoria vigente",
        "póliza obligatoria vigente",
        "no disponer de poliza obligatoria vigente del vehiculo",
        "no disponer de póliza obligatoria vigente del vehículo",
        "no disponer de seguro obligatorio en vigor",
    ], 3)

    text = patch_add_list(text, "casco", [
        "no hacer uso del casco exigido reglamentariamente",
        "casco no debidamente abrochado",
        "casco desabrochado o mal ajustado",
        "casco exigido reglamentariamente",
    ], 3)

    text = patch_add_list(text, "auriculares", [
        "dispositivos acusticos en ambos oidos",
        "dispositivos acústicos en ambos oídos",
        "aparato receptor sonoro en ambos oidos",
        "aparato receptor sonoro en ambos oídos",
        "aparato receptor sonoro",
        "receptor sonoro en ambos oidos",
        "receptor sonoro en ambos oídos",
    ], 3)

    text = patch_add_list(text, "condiciones_vehiculo", [
        "dispositivos luminosos no reglamentarios",
        "ruedas en deficiente estado de uso",
        "componentes mecanicos defectuosos",
        "componentes mecánicos defectuosos",
        "fallo en el sistema de iluminacion",
        "fallo en el sistema de iluminación",
        "deficiencias tecnicas relevantes",
        "deficiencias técnicas relevantes",
        "mantenimiento deficiente del vehiculo",
    ], 3)

    text = patch_add_list(text, "carril", [
        "posicion no ajustada a la configuracion de la calzada",
        "posición no ajustada a la configuración de la calzada",
        "utilizar carril no habilitado para su posicion de marcha",
        "utilizar carril no habilitado para su posición de marcha",
        "carril no habilitado para su posicion de marcha",
        "carril no habilitado para su posición de marcha",
        "ocupar carril inadecuado",
    ], 4)

    text = patch_add_list(text, "atencion", [
        "no conservar atencion plena a la circulacion",
        "no conservar atención plena a la circulación",
        "mantener distraccion continuada",
        "mantener distracción continuada",
        "mantener distraccion continuada durante la marcha comprometiendo el control del vehiculo",
        "mantener distracción continuada durante la marcha comprometiendo el control del vehículo",
        "atencion plena a la circulacion",
        "atención plena a la circulación",
    ], 3)

    text = patch_add_list(text, "movil", [
        "terminal electronico portatil",
        "terminal electrónico portátil",
        "utilizar terminal electronico portatil durante la marcha sin uso de manos libres",
        "utilizar terminal electrónico portátil durante la marcha sin uso de manos libres",
    ], 3)

    text = patch_if_any_block(text, 'if any(s in blob for s in [\n        "casco reglamentario"', [
        "no hacer uso del casco exigido reglamentariamente",
        "casco no debidamente abrochado",
    ])

    text = patch_if_any_block(text, 'if any(s in blob for s in [\n        "uso de dispositivos de audio"', [
        "dispositivos acusticos en ambos oidos",
        "dispositivos acústicos en ambos oídos",
        "aparato receptor sonoro en ambos oidos",
        "aparato receptor sonoro en ambos oídos",
        "aparato receptor sonoro",
    ])

    text = patch_if_any_block(text, 'if any(s in blob for s in [\n        "inspeccion tecnica en vigor"', [
        "inspeccion tecnica del vehiculo caducada",
        "inspección técnica del vehículo caducada",
        "carecer de inspeccion tecnica actualizada",
        "carecer de inspección técnica actualizada",
        "no tener vigente la inspeccion tecnica obligatoria",
        "no tener vigente la inspección técnica obligatoria",
    ])

    text = patch_if_any_block(text, 'if any(s in blob for s in [\n        "carencia de seguro"', [
        "cobertura minima obligatoria",
        "cobertura mínima obligatoria",
        "poliza obligatoria vigente",
        "póliza obligatoria vigente",
        "no disponer de poliza obligatoria vigente del vehiculo",
        "no disponer de póliza obligatoria vigente del vehículo",
    ])

    text = patch_if_any_block(text, 'if any(s in blob for s in [\n        "carril derecho"', [
        "posicion no ajustada a la configuracion de la calzada",
        "posición no ajustada a la configuración de la calzada",
        "utilizar carril no habilitado para su posicion de marcha",
        "utilizar carril no habilitado para su posición de marcha",
        "ocupar carril inadecuado",
    ])

    text = patch_if_any_block(text, 'if any(s in blob for s in [\n        "neumatico"', [
        "dispositivos luminosos no reglamentarios",
        "ruedas en deficiente estado de uso",
        "componentes mecanicos defectuosos",
        "componentes mecánicos defectuosos",
        "fallo en el sistema de iluminacion",
        "fallo en el sistema de iluminación",
        "deficiencias tecnicas relevantes",
        "deficiencias técnicas relevantes",
    ])

    text = patch_if_any_block(text, 'if any(s in blob for s in [\n        "no mantener la atencion"', [
        "no conservar atencion plena a la circulacion",
        "no conservar atención plena a la circulación",
        "mantener distraccion continuada",
        "mantener distracción continuada",
        "mantener distraccion continuada durante la marcha comprometiendo el control del vehiculo",
        "mantener distracción continuada durante la marcha comprometiendo el control del vehículo",
    ])

    text = patch_if_any_block(text, 'if any(s in blob for s in [\n        "telefono movil"', [
        "terminal electronico portatil",
        "terminal electrónico portátil",
        "utilizar terminal electronico portatil durante la marcha sin uso de manos libres",
        "utilizar terminal electrónico portátil durante la marcha sin uso de manos libres",
    ])

    blocker = '''
    # Blindaje: alcohol no debe caer a atención por menciones a agentes o controles
    if any(s in blob for s in [
        "alcohol",
        "alcoholemia",
        "etilometro",
        "etilómetro",
        "test de alcohol",
        "prueba de alcoholemia",
        "control de alcoholemia",
        "aire espirado",
        "mg/l",
        "resultado positivo"
    ]):
        scores["atencion"] = max(0, scores["atencion"] - 12)
'''
    marker = '    if _looks_like_agent_order_case(core):\n        scores["semaforo"] -= 6\n        scores["atencion"] += 4\n'
    if blocker.strip() not in text and marker in text:
        text = text.replace(marker, marker + blocker, 1)

    tie_block = '''
    # Preferencia expresa por alcohol cuando existan señales fuertes
    if scores.get("alcohol", 0) >= 5 and any(s in blob for s in [
        "alcohol",
        "alcoholemia",
        "etilometro",
        "etilómetro",
        "test de alcohol",
        "prueba de alcoholemia",
        "control de alcoholemia",
        "resultado positivo"
    ]):
        return "alcohol"
'''
    marker2 = '    scores = _score_infraction_from_core(core)\n'
    if tie_block.strip() not in text and marker2 in text:
        text = text.replace(marker2, tie_block + "\n" + marker2, 1)

    TARGET.write_text(text, encoding="utf-8")
    print("Parche aplicado correctamente en generate.py")

if __name__ == "__main__":
    main()
