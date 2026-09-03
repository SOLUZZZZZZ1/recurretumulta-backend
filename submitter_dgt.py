from typing import Any, Dict
from xml.sax.saxutils import escape

from rtm_core.runtime_capabilities import require_capability


DGT_ENDPOINT = "https://ws.dgt.es/consultaDEV"  # ⚠️ cambiar por endpoint real


class DGTSubmitter:

    name = "dgt"

    def build_xml(self, case_data: Dict[str, Any]) -> str:
        dni = escape(str(case_data.get("dni_nie", ""))[:32])
        case_id = escape(str(case_data.get("case_id", "AUTO"))[:80])

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
        del xml
        raise NotImplementedError(
            "Firmador DGT legacy retirado; use exclusivamente dgt_client homologado"
        )

    def send_to_dgt(self, signed_xml: str) -> Dict[str, Any]:
        # Incluso una llamada directa a este método queda bloqueada si el
        # entorno no autoriza presentaciones externas.
        require_capability("external_submission")

        del signed_xml
        raise NotImplementedError(
            "Transporte DGT legacy retirado; use exclusivamente dgt_client homologado"
        )

    def submit(self, case_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
        # El guard se ejecuta antes de firmar, preparar un envío o abrir red.
        require_capability("external_submission")

        del case_id, pdf_bytes
        raise NotImplementedError(
            "Submitter DGT legacy retirado; use exclusivamente dgt_client homologado"
        )
