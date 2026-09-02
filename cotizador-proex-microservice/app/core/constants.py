from enum import Enum

class AssociationType(Enum):
    QUOTE_TO_DEAL = 64
    QUOTE_TO_LINE_ITEM = 67
    QUOTE_TO_TEMPLATE = 286


# Ficha del remitente que HubSpot muestra en "Tu empresa" al publicar la
# cotización. Son los mismos valores que el portal inyecta en los quotes
# creados a mano (Ajustes > Predeterminados de la cuenta), replicados aquí
# porque el API no los aplica solo: si no se envían, el quote nace sin
# empresa remitente y el asistente rellena el hueco con el nombre de la
# cuenta del desarrollador.
SENDER_COMPANY_PROPERTIES = {
    "hs_sender_company_name": "Proex Drinks Group S.A.",
    "hs_sender_company_domain": "proexdg.com",
    "hs_sender_company_address": "Avda. Americo Vespucio 5. Cartuja Building A-2ª. Modulos 7 y 8",
    "hs_sender_company_city": "España",
    "hs_sender_company_state": "Sevilla",
    "hs_sender_company_zip": "41092",
    "hs_sender_company_country": "España",
}
