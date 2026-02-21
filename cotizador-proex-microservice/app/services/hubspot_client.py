"""HubSpot API client service.

This module provides a singleton client for interacting with the HubSpot API.
"""

import logging
from functools import lru_cache
from typing import Optional

from hubspot import HubSpot
from app.core.config import settings

logger = logging.getLogger(__name__)


class HubSpotClient:
    """Singleton client for HubSpot API interactions.

    This class provides a centralized way to access the HubSpot API
    with proper configuration and error handling.
    """

    _instance: Optional['HubSpotClient'] = None
    _client: Optional[HubSpot] = None

    def __new__(cls):
        """Ensure only one instance of HubSpotClient exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the HubSpot client with access token."""
        if self._client is None:
            try:
                self._client = HubSpot(access_token=settings.proex_access_token)
                logger.info("HubSpot client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize HubSpot client: {e}")
                raise

    def get_client(self) -> HubSpot:
        """Get the HubSpot client instance.

        Returns:
            Configured HubSpot client.

        Raises:
            RuntimeError: If client is not initialized.
        """
        if self._client is None:
            raise RuntimeError("HubSpot client not initialized")
        return self._client

    def test_connection(self) -> bool:
        """Test the HubSpot API connection.

        Returns:
            True if connection is successful, False otherwise.
        """
        try:
            client = self.get_client()
            # Try to get account info to test connection
            client.crm.owners.get_page(limit=1)
            logger.info("HubSpot connection test successful")
            return True
        except Exception as e:
            logger.error(f"HubSpot connection test failed: {e}")
            return False


@lru_cache()
def get_hubspot_client() -> HubSpotClient:
    """Get cached HubSpot client instance.

    Returns:
        HubSpotClient singleton instance.
    """
    return HubSpotClient()


# Global instance for convenience
hubspot_client = get_hubspot_client()
