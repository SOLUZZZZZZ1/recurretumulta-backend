RTM / RecurreTuMulta — Plantillas DOCX (v1)

Coloca estas plantillas en tu backend (recomendado):
backend/templates/
  dgt/
    dgt_alegaciones_v1.docx
    dgt_reposicion_v1.docx
  generic/
    recurso_admin_base_v1.docx

Marcadores (placeholders)
- Sustituye los valores entre doble llave {{...}} en el generador.
- Bloques largos:
  {{ANTECEDENTES}}
  {{ALEGACIONES}}
  {{FUNDAMENTOS}}
  {{SUPLICO}}
  {{OTROSI}} (solo genérico)

Datos del interesado:
  {{NOMBRE_COMPLETO}}
  {{DNI_NIE}}
  {{DOMICILIO_NOTIF}}
  {{EMAIL}}
  {{TELEFONO_OPCIONAL}}

DGT:
  {{PROVINCIA}}
  {{EXPEDIENTE_DGT}}
  {{MATRICULA}}
  {{IMPORTE}}
  {{FECHA_NOTIFICACION}}

Genérico:
  {{ORGANISMO}}
  {{TIPO_RECURSO}}
  {{EXPEDIENTE_ADMIN}}
  {{FECHA_RESOLUCION}}
  {{FECHA_NOTIFICACION}}

Comunes:
  {{LOCALIDAD}}
  {{FECHA_PRESENTACION}}

Recomendación: guarda en events/documentos el template_key + versión usada para auditoría.
