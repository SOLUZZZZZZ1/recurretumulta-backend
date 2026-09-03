"""Prueba manual DGT; nunca produce red al importarse."""

import requests

from rtm_core.runtime_capabilities import require_capability


def main() -> int:
    require_capability("external_submission")
    with open("consulta_dev_real_signed.xml", "r", encoding="utf-8") as handle:
        xml_body = handle.read()
    response = requests.post(
        "https://prewww.dgt.es/WS_NTRA/consultaDEV",
        data=xml_body.encode("utf-8"),
        headers={"Content-Type": "text/xml;charset=UTF-8"},
        timeout=20,
        allow_redirects=False,
    )
    # El cuerpo puede contener identificadores o evidencia; no se imprime.
    print("STATUS:", response.status_code)
    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
