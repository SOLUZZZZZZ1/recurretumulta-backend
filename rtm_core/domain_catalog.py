"""Catálogo transversal de satélites, familias y primer rumbo RTM.

El catálogo describe capacidades y lenguaje operativo. No extrae documentos,
no resuelve una familia y no redacta escritos. Su finalidad es permitir que el
CORE crezca por registros, sin convertir la aplicación en una cadena de
``if/elif`` por cada nuevo servicio.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from rtm_core.service_catalog import canonical_department


DOMAIN_CATALOG_VERSION = "rtm_domain_catalog_v1_0"
DepartmentCode = Literal[
    "traffic",
    "debt",
    "administration",
    "travel",
    "claims",
    "other",
]
CapabilityState = Literal["specialist_ready", "orientation_only"]


class ServiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    department: DepartmentCode
    label: str = Field(min_length=1)
    review_amount_cents: int = Field(ge=0)
    initial_direction: str = Field(min_length=1)
    alternatives: tuple[str, ...] = ()


class FamilyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    department: DepartmentCode
    family: str = Field(min_length=1)
    label: str = Field(min_length=1)
    specialist: str = Field(min_length=1)
    focus: str = Field(min_length=1)
    capability: CapabilityState = "orientation_only"


_SERVICE_PROFILES: dict[str, ServiceProfile] = {
    "traffic": ServiceProfile(
        department="traffic",
        label="Tráfico y movilidad",
        review_amount_cents=1000,
        initial_direction=(
            "Comprobar el hecho denunciado, la fase procedimental, la fecha de "
            "notificación, el plazo y la prueba técnica o documental antes de "
            "seleccionar el especialista de tráfico."
        ),
        alternatives=(
            "Solicitar o completar la documentación probatoria necesaria.",
            "Revisar una vía subsidiaria cuando la impugnación principal no sea suficiente.",
        ),
    ),
    "debt": ServiceProfile(
        department="debt",
        label="Morosidad y deudas",
        review_amount_cents=1000,
        initial_direction=(
            "Identificar acreedor, deudor, origen, cuantía, vencimiento y prueba "
            "de la deuda; después decidir entre requerimiento, negociación, "
            "reclamación judicial o defensa frente a la reclamación."
        ),
        alternatives=(
            "Completar facturas, contrato, justificantes y comunicaciones previas.",
            "Valorar negociación o reconocimiento de deuda antes de una actuación judicial.",
        ),
    ),
    "administration": ServiceProfile(
        department="administration",
        label="Administración pública",
        review_amount_cents=2500,
        initial_direction=(
            "Identificar el acto administrativo, el órgano, la fase, la fecha de "
            "notificación y el recurso o trámite disponible, con revisión OPS "
            "reforzada por la complejidad y los plazos."
        ),
        alternatives=(
            "Solicitar acceso al expediente o documentación administrativa faltante.",
            "Valorar subsanación, alegaciones, recurso o actuación posterior según la fase.",
        ),
    ),
    "travel": ServiceProfile(
        department="travel",
        label="Viajes y transporte de pasajeros",
        review_amount_cents=1000,
        initial_direction=(
            "Reconstruir reserva, proveedor, trayecto o estancia, incidencia, "
            "comunicaciones, gastos y solución solicitada antes de orientar la "
            "reclamación frente a la empresa responsable."
        ),
        alternatives=(
            "Reclamar reembolso, compensación, asistencia o gastos según los hechos acreditados.",
            "Dirigir la reclamación a transportista, agencia, hotel, aseguradora o medio de pago.",
        ),
    ),
    "claims": ServiceProfile(
        department="claims",
        label="Reclamaciones de consumo y servicios",
        review_amount_cents=1000,
        initial_direction=(
            "Identificar contrato, proveedor, incumplimiento, reclamaciones previas, "
            "perjuicio y resultado solicitado para decidir la vía de reclamación."
        ),
        alternatives=(
            "Completar contrato, facturas, publicidad y comunicaciones con el proveedor.",
            "Valorar negociación, servicio de atención, organismo sectorial o reclamación formal.",
        ),
    ),
    "other": ServiceProfile(
        department="other",
        label="Otros asuntos",
        review_amount_cents=1000,
        initial_direction=(
            "Ordenar los hechos y documentos, identificar el área responsable y "
            "mantener el expediente en revisión hasta asignar una familia concreta."
        ),
        alternatives=(
            "Pedir la documentación mínima que permita encuadrar el asunto.",
            "Escalar a revisión OPS cuando no exista todavía una familia registrada.",
        ),
    ),
}


def _family(
    department: DepartmentCode,
    family: str,
    label: str,
    specialist: str,
    focus: str,
    *,
    ready: bool = False,
) -> FamilyProfile:
    return FamilyProfile(
        department=department,
        family=family,
        label=label,
        specialist=specialist,
        focus=focus,
        capability="specialist_ready" if ready else "orientation_only",
    )


_FAMILY_ITEMS = (
    # Tráfico: especialistas profundos ya validados y familias de crecimiento.
    _family("traffic", "velocidad", "Exceso de velocidad", "traffic.velocidad", "Revisar medición, límite, equipo, trazabilidad y prueba.", ready=True),
    _family("traffic", "semaforo", "Semáforo en rojo", "traffic.semaforo", "Revisar fase roja, línea de detención, imágenes y secuencia probatoria.", ready=True),
    _family("traffic", "temeraria", "Conducción temeraria", "traffic.temeraria", "Concretar la maniobra, el riesgo grave, la prueba y la proporcionalidad.", ready=True),
    _family("traffic", "atencion", "Falta de atención o negligencia", "traffic.atencion", "Precisar la conducta atribuida y evitar confundirla con temeraria."),
    _family("traffic", "movil", "Uso de teléfono móvil", "traffic.movil", "Comprobar uso manual, observación, imagen y descripción del agente."),
    _family("traffic", "auriculares", "Uso de auriculares", "traffic.auriculares", "Comprobar el dispositivo, la conducta observada y la prueba disponible."),
    _family("traffic", "cinturon", "Cinturón de seguridad", "traffic.cinturon", "Revisar ocupante, obligación, observación y prueba."),
    _family("traffic", "casco", "Casco obligatorio", "traffic.casco", "Revisar vehículo, usuario, obligación y prueba."),
    _family("traffic", "alcohol", "Alcohol", "traffic.alcohol", "Revisar tasas, pruebas, tiempos, garantías y documentación técnica."),
    _family("traffic", "drogas", "Drogas", "traffic.drogas", "Revisar prueba indiciaria, confirmación, cadena de custodia y garantías."),
    _family("traffic", "seguro", "Seguro obligatorio", "traffic.seguro", "Comprobar vigencia, vehículo, titularidad y consultas administrativas."),
    _family("traffic", "itv", "Inspección técnica", "traffic.itv", "Comprobar estado ITV, fechas, vehículo y circunstancias de circulación."),
    _family("traffic", "marcas_viales", "Marcas viales", "traffic.marcas_viales", "Identificar la marca, maniobra, señalización y prueba."),
    _family("traffic", "carril", "Uso de carril o arcén", "traffic.carril", "Concretar carril, tramo, maniobra, excepciones y prueba."),
    _family("traffic", "estacionamiento", "Estacionamiento", "traffic.estacionamiento", "Revisar lugar, señalización, horario, autorización y fotografías."),
    _family("traffic", "zona_restringida", "Zona restringida o bajas emisiones", "traffic.zona_restringida", "Revisar acceso, señalización, matrícula, autorización y captación."),
    _family("traffic", "retirada_vehiculo", "Retirada o baja de vehículo", "traffic.retirada_vehiculo", "Identificar trámite, titularidad, destino del vehículo y documentos exigibles."),
    _family("traffic", "neumaticos", "Neumáticos", "traffic.neumaticos", "Concretar defecto, medición, rueda afectada y prueba."),
    _family("traffic", "peso", "Exceso de peso", "traffic.peso", "Revisar pesaje, tolerancias, vehículo, carga y documentación."),
    _family("traffic", "tacografo", "Tacógrafo y tiempos", "traffic.tacografo", "Revisar registros, periodo, conductor, empresa y documentación técnica."),
    _family("traffic", "documentacion_transporte", "Documentación de transporte", "traffic.documentacion_transporte", "Precisar documento exigido, operación y sujeto responsable."),
    _family("traffic", "adr", "Mercancías peligrosas", "traffic.adr", "Precisar mercancía, obligación ADR, sujeto responsable y prueba."),

    # Morosidad y deudas.
    _family("debt", "factura_impagada", "Factura impagada", "debt.unpaid_invoice", "Verificar prestación, factura, vencimiento, aceptación y saldo pendiente.", ready=True),
    _family("debt", "alquiler_impagado", "Alquiler impagado", "debt.unpaid_rent", "Verificar contrato, mensualidades, suministros, pagos y comunicaciones."),
    _family("debt", "prestamo_deuda", "Préstamo o deuda reconocida", "debt.loan_or_acknowledgement", "Verificar entrega, devolución pactada, vencimiento y reconocimiento."),
    _family("debt", "requerimiento_pago", "Requerimiento de pago", "debt.payment_demand", "Preparar cuantía, concepto, vencimiento, documentos y canal fehaciente."),
    _family("debt", "monitorio", "Procedimiento monitorio", "debt.payment_order", "Comprobar deuda dineraria, vencida, exigible y documentalmente acreditada."),
    _family("debt", "oposicion_deudor", "Oposición o defensa del deudor", "debt.debtor_defence", "Identificar deuda discutida, pagos, defectos, prescripción alegada y documentos."),
    _family("debt", "insolvencia", "Insolvencia", "debt.insolvency", "Comprobar situación patrimonial, procedimientos y utilidad de la reclamación."),
    _family("debt", "fichero_solvencia", "Fichero de solvencia o ASNEF", "debt.credit_file", "Verificar inclusión, deuda, requerimiento previo, consulta, rectificación y baja."),
    _family("debt", "negociacion_deuda", "Negociación o acuerdo de pago", "debt.settlement", "Definir saldo, capacidad de pago, calendario, garantías y cierre documental."),

    # Administración pública.
    _family("administration", "sancion_administrativa", "Sanción administrativa", "administration.sanction", "Identificar hecho, norma, prueba, fase, plazo y órgano."),
    _family("administration", "requerimiento", "Requerimiento administrativo", "administration.requirement", "Precisar qué se exige, plazo, documentos y consecuencias del incumplimiento."),
    _family("administration", "apremio_recaudacion", "Apremio o recaudación", "administration.enforcement", "Revisar deuda, providencia, notificaciones, recargos y fase recaudatoria.", ready=True),
    _family("administration", "tributos", "Tributos", "administration.tax", "Identificar impuesto, acto, periodo, liquidación, alegaciones y recurso."),
    _family("administration", "licencia", "Licencia o autorización", "administration.licence", "Revisar solicitud, requisitos, resolución, condicionantes y plazo."),
    _family("administration", "subvencion", "Subvención", "administration.grant", "Revisar convocatoria, solicitud, justificación, reintegro y resolución."),
    _family("administration", "responsabilidad_patrimonial", "Responsabilidad patrimonial", "administration.liability", "Concretar daño, funcionamiento público, causalidad, cuantía y plazo."),
    _family("administration", "silencio_administrativo", "Silencio administrativo", "administration.silence", "Comprobar solicitud, registro, plazo de resolución y efecto aplicable."),
    _family("administration", "recurso_administrativo", "Recurso administrativo", "administration.appeal", "Identificar acto, firmeza, recurso disponible, plazo y pretensión."),

    # Viajes y transporte de pasajeros.
    _family("travel", "vuelo_cancelado", "Cancelación de vuelo", "travel.flight_cancelled", "Verificar reserva, cancelación, aviso, alternativa, reembolso y gastos."),
    _family("travel", "retraso_vuelo", "Retraso de vuelo", "travel.flight_delay", "Verificar horarios, llegada, causa comunicada, asistencia y gastos."),
    _family("travel", "denegacion_embarque", "Denegación de embarque", "travel.denied_boarding", "Verificar presentación, documentación, sobreventa, alternativa y compensación solicitada."),
    _family("travel", "equipaje", "Equipaje", "travel.baggage", "Verificar facturación, parte de irregularidad, entrega, daños, contenido y gastos."),
    _family("travel", "hotel", "Hotel o alojamiento", "travel.hotel", "Verificar reserva, condiciones, incumplimiento, reclamación y solución ofrecida."),
    _family("travel", "viaje_combinado", "Viaje combinado", "travel.package", "Identificar organizador, minorista, servicios incluidos, cambio o incumplimiento."),
    _family("travel", "agencia_plataforma", "Agencia o plataforma", "travel.agency", "Distinguir intermediario y proveedor, reserva, cobro y responsabilidad reclamada."),
    _family("travel", "seguro_viaje", "Seguro de viaje", "travel.insurance", "Revisar póliza, siniestro, comunicación, exclusiones y respuesta de la aseguradora."),

    # Reclamaciones generales.
    _family("claims", "consumo", "Consumo general", "claims.consumer", "Verificar compra o servicio, defecto, reclamación previa y solución solicitada."),
    _family("claims", "telecomunicaciones", "Telecomunicaciones", "claims.telecommunications", "Revisar contrato, facturación, baja, portabilidad, avería y reclamaciones."),
    _family("claims", "energia", "Energía y suministros", "claims.energy", "Revisar contrato, lecturas, facturas, incidencias y respuesta del comercializador o distribuidor."),
    _family("claims", "seguros", "Seguros", "claims.insurance", "Revisar póliza, siniestro, peritación, oferta, rechazo y comunicaciones."),
    _family("claims", "banca", "Banca y medios de pago", "claims.banking", "Revisar contrato, operación, autenticación, reclamación y respuesta de la entidad."),
    _family("claims", "comercio_electronico", "Comercio electrónico", "claims.ecommerce", "Revisar pedido, pago, entrega, desistimiento, devolución y comunicaciones."),
    _family("claims", "servicios_profesionales", "Servicios profesionales", "claims.professional_services", "Revisar encargo, alcance, factura, ejecución y perjuicio alegado."),

    _family("other", "revision_general", "Revisión general", "other.general", "Ordenar hechos y documentos antes de asignar un satélite especializado."),
)

_FAMILY_PROFILES: dict[tuple[str, str], FamilyProfile] = {
    (item.department, item.family): item for item in _FAMILY_ITEMS
}


def service_profile(
    department: str | None,
    case_type: str | None = None,
    category: str | None = None,
) -> ServiceProfile:
    canonical = canonical_department(department, case_type, category)
    return _SERVICE_PROFILES[canonical]


def family_profile(
    department: str | None,
    family: str | None,
    *,
    case_type: str | None = None,
    category: str | None = None,
) -> Optional[FamilyProfile]:
    canonical = canonical_department(department, case_type, category)
    family_code = str(family or "").strip().lower()
    if not family_code:
        return None
    return _FAMILY_PROFILES.get((canonical, family_code))


def registered_family_codes(department: str | None = None) -> tuple[str, ...]:
    if department is None:
        return tuple(sorted({item.family for item in _FAMILY_ITEMS}))
    canonical = canonical_department(department)
    return tuple(
        sorted(item.family for item in _FAMILY_ITEMS if item.department == canonical)
    )


def catalog_snapshot() -> dict[str, object]:
    return {
        "version": DOMAIN_CATALOG_VERSION,
        "services": {
            code: profile.model_dump(mode="json")
            for code, profile in sorted(_SERVICE_PROFILES.items())
        },
        "families": [
            item.model_dump(mode="json")
            for item in sorted(_FAMILY_ITEMS, key=lambda value: (value.department, value.family))
        ],
    }
