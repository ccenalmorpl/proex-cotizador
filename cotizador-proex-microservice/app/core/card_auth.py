"""Auth para los endpoints de negocio que llama la card (no los webhooks de HubSpot)."""

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)


async def verify_card_secret(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Valida Authorization: Bearer <PROEX_CARD_SECRET> mandado por la card.

    hubspot.fetch() descarta cualquier header custom salvo Authorization, por
    eso es el unico vehiculo posible para este secreto.

    En local (settings.is_production False, es decir DEBUG=True) no se
    exige, para poder probar desde /docs sin cabecera.

    Errores:
      - 503 si PROEX_CARD_SECRET no esta configurado en el server.
      - 401 si el header falta o no coincide. Mensaje generico.
    """
    if not settings.is_production:
        return

    expected = settings.proex_card_secret
    if not expected:
        logger.error("[card_auth] PROEX_CARD_SECRET no configurado en el server")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "card_secret_not_configured"},
        )

    raw = authorization or ""
    received = raw[7:] if raw.startswith("Bearer ") else raw
    if not hmac.compare_digest(received, expected):
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(f"[card_auth] secreto invalido o ausente desde ip={client_ip}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_or_missing_card_secret"},
        )
