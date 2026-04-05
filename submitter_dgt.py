import os
import subprocess
import tempfile
import requests
from typing import Dict, Any

DGT_ENDPOINT = "https://ws.dgt.es/consultaDEV"  # ⚠️ cambiar por endpoint real


class DGTSubmitter:

    name = "dgt"

    def build_xml(self, case_data: Dict[str, Any]) -> str:
        dni = case_data.get("dni_nie", "")
        case_id = case_data.get("case_id", "AUTO")

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Peticion xmlns="http://www.dgt.es/nostra/esquemas/consultaDEV/peticion">
  <Atributos>
    <IdPeticion>{case_id}</IdPeticion>
    <NumElementos>1</NumElementos>
    <TimeStamp>2026-01-01T00:00:00</TimeStamp>
    <CodigoCertificado>NTRA0002</CodigoCertificado>
  </Atributos>

  <Solicitudes>
    <SolicitudTransmision>
      <DatosGenericos>
        <Emisor>
          <NifEmisor>Q2826004J</NifEmisor>
          <NombreEmisor>Dirección General de Tráfico</NombreEmisor>
        </Emisor>

        <Solicitante>
          <IdentificadorSolicitante>B75440115</IdentificadorSolicitante>
          <NombreSolicitante>LA TALAMANQUINA SL</NombreSolicitante>
          <Finalidad>Recurso multa</Finalidad>
          <Consentimiento>Ley</Consentimiento>
        </Solicitante>

        <Titular>
          <TipoDocumentacion>DNI</TipoDocumentacion>
          <Documentacion>{dni}</Documentacion>
        </Titular>

        <Transmision>
          <CodigoCertificado>NTRA0002</CodigoCertificado>
          <IdSolicitud>{case_id}</IdSolicitud>
        </Transmision>

      </DatosGenericos>
    </SolicitudTransmision>
  </Solicitudes>
</Peticion>
"""
        return xml

    def sign_xml(self, xml: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.xml")
            output_path = os.path.join(tmp, "signed.xml")

            with open(input_path, "w", encoding="utf-8") as f:
                f.write(xml)

            # 🔥 LLAMADA A TU JAVA
            subprocess.run(
                ["java", "XmlSignerReal", input_path, output_path],
                check=True
            )

            with open(output_path, "r", encoding="utf-8") as f:
                return f.read()

    def send_to_dgt(self, signed_xml: str) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/xml"
        }

        resp = requests.post(
            DGT_ENDPOINT,
            data=signed_xml.encode("utf-8"),
            headers=headers,
            timeout=20
        )

        return {
            "status_code": resp.status_code,
            "response": resp.text
        }

    def submit(self, case_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
        # 🔥 aquí podrías meter datos reales del caso
        case_data = {
            "case_id": case_id,
            "dni_nie": "00000000X"  # ⚠️ sustituir por real
        }

        xml = self.build_xml(case_data)
        signed_xml = self.sign_xml(xml)
        result = self.send_to_dgt(signed_xml)

        return result