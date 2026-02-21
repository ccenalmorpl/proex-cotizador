"""
Helper functions for HubSpot quote creation, associations, and configuration.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from app.core.constants import AssociationType

logger = logging.getLogger(__name__)


# ============================================================
# 1. Build association object for v3 quote create
# ============================================================

def build_association(object_id: str, association_type: AssociationType) -> Dict[str, Any]:
    """
    Builds an association entry for HubSpot quote creation (v3).

    Args:
        object_id: ID of object to associate to the quote
        association_type: Enum of AssociationType

    Returns:
        Association dictionary usable in v3 quote creation
    """
    return {
        "to": {"id": object_id},
        "types": [
            {
                "associationCategory": "HUBSPOT_DEFINED",
                "associationTypeId": association_type.value
            }
        ]
    }


# ============================================================
# 2. Create Quote via HubSpot API v3 with associations
# ============================================================

def create_quote_via_api(
    access_token: str,
    properties: Dict[str, Any],
    associations: List[Dict[str, Any]],
) -> (Optional[str], Optional[str]):
    """
    Create a quote via HubSpot's v3 API.

    Args:
        access_token: OAuth token
        properties: Quote properties dict
        associations: List of associations built by build_association()

    Returns:
        (quote_id, error_message)
        quote_id = string if created successfully
        error_message = string if there was an error, otherwise None
    """
    url = "https://api.hubapi.com/crm/v3/objects/quote"

    payload = {
        "properties": properties,
        "associations": associations
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code >= 300:
        logger.error(f"[create_quote_via_api] HubSpot error: {response.text}")
        return None, response.text

    try:
        quote_id = response.json()["id"]
        return quote_id, None
    except Exception as e:
        logger.error(f"[create_quote_via_api] Could not parse quote ID: {e}")
        return None, f"Could not parse quote ID: {e}"


# ============================================================
# 3. Generic association helper (API v4)
# ============================================================

def associate_object(
    access_token: str,
    from_object: str,
    from_id: str,
    to_object: str,
    to_id: str
) -> bool:
    """
    Associates two CRM records via HubSpot v4 associations API.

    Args:
        access_token: OAuth token
        from_object: "quotes", "deals", "line_items", etc.
        from_id: ID of origin object
        to_object: "line_items", "contacts", "companies", etc.
        to_id: ID of destination object

    Returns:
        True if association successful
    """
    url = f"https://api.hubapi.com/crm/v4/objects/{from_object}/{from_id}/associations/default/{to_object}/{to_id}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.put(url, headers=headers)

    if response.status_code >= 300:
        logger.error(f"[associate_object] Error associating {from_object}:{from_id} -> {to_object}:{to_id}: {response.text}")
        return False

    return True


# ============================================================
# 4. Helper: Get deal associations (contacts, companies, etc.)
# ============================================================

def get_deal_associations(
    access_token: str,
    deal_id: str,
    object_type: str
) -> List[str]:
    """
    Retrieve IDs of objects associated with a deal.

    Args:
        access_token: OAuth token
        deal_id: HubSpot deal ID
        object_type: "contacts", "companies", etc.

    Returns:
        List of associated object IDs
    """
    url = f"https://api.hubapi.com/crm/v4/objects/deals/{deal_id}/associations/default/{object_type}"

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(url, headers=headers)

    if response.status_code >= 300:
        logger.error(f"[get_deal_associations] Error: {response.text}")
        return []

    try:
        results = response.json().get("results", [])
        return [r["toObjectId"] for r in results]
    except Exception:
        return []


# ============================================================
# 5. Configure signers (electronic signature)
# ============================================================

def configure_quote_signers(
    access_token: str,
    quote_id: str,
    contact_ids: List[str]
) -> bool:
    """
    Configure signature signers for electronic signature quotes.

    Args:
        access_token: OAuth token
        quote_id: Quote ID
        contact_ids: list of contact IDs

    Returns:
        True if signers configured successfully
    """
    url = f"https://api.hubapi.com/crm/v4/objects/quote/{quote_id}/associations/contact"

    # associationTypeId=702 is "quote signer"
    payload = [
        {
            "associationCategory": "HUBSPOT_DEFINED",
            "associationTypeId": 702
        }
    ]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    success = True

    for contact_id in contact_ids:
        response = requests.put(
            f"{url}/{contact_id}",
            json=payload,
            headers=headers
        )
        if response.status_code >= 300:
            logger.error(f"[configure_quote_signers] Failed to associate signer {contact_id}: {response.text}")
            success = False

    return success


# ============================================================
# 6. Quote property builder (used in create_quote)
# ============================================================

def build_quote_properties(
    quote_title: str,
    quote_status: str,
    deal_currency: str,
    owner_info: Optional[Dict[str, Any]],
    company_name: Optional[str],
    enable_esign: bool,
    deal_id: str
) -> Dict[str, Any]:
    """
    Build the property dict required for quote creation.

    Args:
        quote_title: Title of the quote
        quote_status: DRAFT / APPROVAL_NOT_NEEDED / etc.
        deal_currency: Currency code (EUR, USD, etc.)
        owner_info: Owner info dict
        company_name: Company name
        enable_esign: Whether esigning is enabled
        deal_id: Deal ID

    Returns:
        Dict of properties for the API
    """
    props = {
        "hs_title": quote_title,
        "hs_status": quote_status,
        "hs_currency": deal_currency
    }

    if enable_esign:
        props["hs_esign_enabled"] = "true"

    if company_name:
        props["hs_company_name"] = company_name

    if owner_info and owner_info.get("email"):
        props["hs_sender_email"] = owner_info["email"]

    return props


def normalize_hs_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    try:
        if date_str.endswith("Z"):
            date_str = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(date_str)
        return dt.date().isoformat()
    except Exception:
        return date_str.split("T")[0]


# ============================================================
# 7. Create Line Item via HubSpot API v3
# ============================================================

def create_line_item_via_api(
    access_token: str,
    properties: Dict[str, Any]
) -> (Optional[str], Optional[str]):
    """
    Create a line item via HubSpot's v3 API.

    Args:
        access_token: OAuth token
        properties: Line item properties dict

    Returns:
        (line_item_id, error_message)
        line_item_id = string if created successfully
        error_message = string if there was an error, otherwise None
    """
    url = "https://api.hubapi.com/crm/v3/objects/line_items"

    payload = {
        "properties": properties
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)

        if response.status_code >= 300:
            error_msg = f"HTTP {response.status_code}: {response.text}"
            logger.error(f"[create_line_item_via_api] HubSpot error: {error_msg}")
            return None, error_msg

        line_item_id = response.json()["id"]
        logger.info(f"[create_line_item_via_api] Created line item {line_item_id}")
        return line_item_id, None

    except Exception as e:
        error_msg = f"Exception creating line item: {str(e)}"
        logger.error(f"[create_line_item_via_api] {error_msg}", exc_info=True)
        return None, error_msg


# ============================================================
# 8. Associate Line Item to Quote via HubSpot API v4
# ============================================================

def associate_line_item_to_quote_v4(
    access_token: str,
    quote_id: str,
    line_item_id: str
) -> (bool, Optional[str]):
    """
    Associate a line item to a quote using HubSpot's v4 Associations API.

    Uses default unlabeled association between quote and line item.

    Args:
        access_token: OAuth token
        quote_id: Quote ID
        line_item_id: Line item ID

    Returns:
        (success_bool, error_message)
        success_bool = True if association successful
        error_message = string if there was an error, otherwise None
    """
    url = f"https://api.hubapi.com/crm/v4/objects/quotes/{quote_id}/associations/default/line_items/{line_item_id}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.put(url, headers=headers, timeout=10)

        if response.status_code >= 300:
            error_msg = f"HTTP {response.status_code}: {response.text}"
            logger.error(
                f"[associate_line_item_to_quote_v4] Error associating line item {line_item_id} "
                f"to quote {quote_id}: {error_msg}"
            )
            return False, error_msg

        logger.info(
            f"[associate_line_item_to_quote_v4] Associated line item {line_item_id} to quote {quote_id}"
        )
        return True, None

    except Exception as e:
        error_msg = f"Exception associating line item: {str(e)}"
        logger.error(
            f"[associate_line_item_to_quote_v4] {error_msg}",
            exc_info=True
        )
        return False, error_msg