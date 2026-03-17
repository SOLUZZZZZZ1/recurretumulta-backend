
import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, Any, List


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "familias_v2.json"


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def normalize_text(text: str) -> str:
    if text is None:
        return ""
    text = str(text).upper()
    text = _strip_accents(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_classifier_config(config_path: str | None = None) -> Dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _init_scores(cfg: Dict[str, Any]) -> Dict[str, int]:
    return {family: 0 for family in cfg.get("familias", {}).keys()}


def score_hecho(hecho: str, cfg: Dict[str, Any]) -> Dict[str, int]:
    text = normalize_text(hecho)
    familias = cfg.get("familias", {})
    reglas_extra = cfg.get("reglas_extra", [])
    scores = _init_scores(cfg)

    for family, meta in familias.items():
        boost = int(meta.get("boost", 1))
        keywords = meta.get("keywords", []) or []
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if kw_norm and kw_norm in text:
                scores[family] += boost

    for rule in reglas_extra:
        required = [normalize_text(x) for x in rule.get("si_contiene_todos", [])]
        family = rule.get("familia")
        rule_score = int(rule.get("score", 0))
        if family and required and all(token in text for token in required):
            scores[family] = max(scores.get(family, 0), rule_score)

    return scores


def detect_family(hecho: str, cfg: Dict[str, Any]) -> tuple[str, Dict[str, int]]:
    scores = score_hecho(hecho, cfg)
    minimum = int(cfg.get("fallback", {}).get("minimum_score_to_classify", 1))
    generic_family = "generic"

    non_generic = {k: v for k, v in scores.items() if k != generic_family}
    best_non_generic = max(non_generic.items(), key=lambda kv: kv[1], default=(generic_family, 0))

    if best_non_generic[1] >= minimum:
        return best_non_generic[0], scores

    best_any = max(scores.items(), key=lambda kv: kv[1], default=(generic_family, 0))
    if best_any[1] <= 0:
        return generic_family, scores

    if cfg.get("fallback", {}).get("prefer_non_generic_on_tie", True):
        non_generic_max = max(non_generic.values(), default=0)
        generic_score = scores.get(generic_family, 0)
        if non_generic_max == generic_score and non_generic_max > 0:
            for family, value in non_generic.items():
                if value == non_generic_max:
                    return family, scores

    return best_any[0], scores


def classify_hecho(hecho: str, cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = cfg or load_classifier_config()
    familia_detectada, scores = detect_family(hecho, cfg)
    return {
        "hecho": hecho,
        "familia_detectada": familia_detectada,
        "scores": scores,
        "hecho_normalizado": normalize_text(hecho),
    }


def test_cases(payload: Dict[str, Any], cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = cfg or load_classifier_config()
    casos: List[Dict[str, Any]] = payload.get("casos", []) or []

    resultados = []
    aciertos = 0

    for caso in casos:
        hecho = caso.get("hecho", "")
        familia_esperada = caso.get("familia_esperada", "generic")

        clas = classify_hecho(hecho, cfg)
        correcto = clas["familia_detectada"] == familia_esperada
        if correcto:
            aciertos += 1

        resultados.append({
            "hecho": hecho,
            "familia_esperada": familia_esperada,
            "familia_detectada": clas["familia_detectada"],
            "correcto": correcto,
            "hecho_para_recurso": hecho,
            "scores": clas["scores"],
        })

    total = len(resultados)
    fallos = total - aciertos
    accuracy = (aciertos / total) if total else 0.0

    return {
        "ok": True,
        "total": total,
        "aciertos": aciertos,
        "fallos": fallos,
        "accuracy": accuracy,
        "resultados": resultados,
    }


if __name__ == "__main__":
    cfg = load_classifier_config()
    ejemplo = {
        "casos": [
            {"hecho": "INSPECCIÓN TÉCNICA CADUCADA", "familia_esperada": "itv"},
            {"hecho": "UTILIZAR DISPOSITIVOS DE AUDIO EN AMBOS OÍDOS", "familia_esperada": "auriculares"},
            {"hecho": "CONDUCIR CON TASA SUPERIOR A LA PERMITIDA", "familia_esperada": "alcohol"},
            {"hecho": "NO RESPETAR POSICIÓN EN CALZADA", "familia_esperada": "carril"},
        ]
    }
    print(json.dumps(test_cases(ejemplo, cfg), ensure_ascii=False, indent=2))
