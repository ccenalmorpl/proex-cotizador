import logging
from functools import lru_cache
from typing import Optional

from hubspot import HubSpot
from app.core.config import settings

logger = logging.getLogger(__name__)

class HubSpotClient:
    _instance: Optional['HubSpotClient'] = None
    _client: Optional[HubSpot] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._client is None:
            try:
                self._client = HubSpot(access_token=settings.proex_access_token)
                logger.info("HubSpot client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize HubSpot client: {e}")
                raise

    def get_client(self) -> HubSpot:
        if self._client is None:
            raise RuntimeError("HubSpot client not initialized")
        return self._client

    def test_connection(self) -> bool:
        try:
            client = self.get_client()
            client.crm.owners.get_page(limit=1)
            logger.info("HubSpot connection test successful")
            return True
        except Exception as e:
            logger.error(f"HubSpot connection test failed: {e}")
            return False

@lru_cache()
def get_hubspot_client() -> HubSpotClient:
    return HubSpotClient()

hubspot_client = get_hubspot_client()
