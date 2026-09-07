import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from app.core.constants import AssociationType
from app.utils.property_helpers import enforce_line_item_price_rounding

logger = logging.getLogger(__name__)

LINE_ITEMS_BATCH_LIMIT = 100


def build_association(object_id: str, association_type: AssociationType) -> Dict[str, Any]:
    """
    Builds an association entry for HubSpot quote creation (v3).
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


def create_quote_via_api(
    access_token: str,
    properties: Dict[str, Any],
    associations: List[Dict[str, Any]],
) -> (Optional[str], Optional[str]):
    """
    Create a quote via HubSpot's v3 API.
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


def associate_object(
    access_token: str,
    from_object: str,
    from_id: str,
    to_object: str,
    to_id: str
) -> bool:
    """
    Associates two CRM records via HubSpot v4 associations API.
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


def get_deal_associations(
    access_token: str,
    deal_id: str,
    object_type: str
) -> List[str]:
    """
    Retrieve IDs of objects associated with a deal.
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


def configure_quote_signers(
    access_token: str,
    quote_id: str,
    contact_ids: List[str]
) -> bool:
    """
    Configure signature signers for electronic signature quotes.
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


def create_line_item_via_api(
    access_token: str,
    properties: Dict[str, Any]
) -> (Optional[str], Optional[str]):
    """
    Create a line item via HubSpot's v3 API.
    """
    url = "https://api.hubapi.com/crm/v3/objects/line_items"

    enforce_line_item_price_rounding(properties)

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


def associate_line_item_to_quote_v4(
    access_token: str,
    quote_id: str,
    line_item_id: str
) -> (bool, Optional[str]):
    """
    Associate a line item to a quote using HubSpot's v4 Associations API.
    Uses default unlabeled association between quote and line item.
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


def batch_create_line_items_via_api(
    access_token: str,
    items: List[Dict[str, Any]],
    chunk_size: int = LINE_ITEMS_BATCH_LIMIT,
    on_chunk_done: Optional[callable] = None,
) -> Tuple[List[str], List[str]]:
    """Crea N line items en chunks de hasta 100 vía batch API.

    Devuelve (created_ids, errors). Los IDs se devuelven en el mismo orden que `items`.
    Si un chunk falla globalmente, se registra el error y se continúa con el siguiente.
    `on_chunk_done(created_so_far)` se invoca tras cada chunk OK para reportar progreso.
    """
    url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    created_ids: List[str] = []
    errors: List[str] = []

    for chunk_start in range(0, len(items), chunk_size):
        chunk = items[chunk_start:chunk_start + chunk_size]
        for props in chunk:
            enforce_line_item_price_rounding(props)

        payload = {"inputs": [{"properties": props} for props in chunk]}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
        except Exception as e:
            error_msg = f"Chunk [{chunk_start}:{chunk_start + len(chunk)}] exception: {e}"
            logger.error(f"[batch_create_line_items_via_api] {error_msg}", exc_info=True)
            errors.append(error_msg)
            continue

        if response.status_code >= 300:
            error_msg = f"Chunk [{chunk_start}:{chunk_start + len(chunk)}] HTTP {response.status_code}: {response.text}"
            logger.error(f"[batch_create_line_items_via_api] {error_msg}")
            errors.append(error_msg)
            continue

        try:
            results = response.json().get("results", [])
        except Exception as e:
            error_msg = f"Chunk [{chunk_start}:{chunk_start + len(chunk)}] could not parse response: {e}"
            logger.error(f"[batch_create_line_items_via_api] {error_msg}")
            errors.append(error_msg)
            continue

        chunk_ids = [str(r["id"]) for r in results if r.get("id")]
        created_ids.extend(chunk_ids)
        logger.info(
            f"[batch_create_line_items_via_api] Chunk [{chunk_start}:{chunk_start + len(chunk)}] "
            f"created {len(chunk_ids)} line items"
        )

        if on_chunk_done:
            try:
                on_chunk_done(len(created_ids))
            except Exception as cb_err:
                logger.warning(f"[batch_create_line_items_via_api] on_chunk_done failed: {cb_err}")

    return created_ids, errors


def batch_associate_line_items_to_quote_v4(
    access_token: str,
    quote_id: str,
    line_item_ids: List[str],
    chunk_size: int = LINE_ITEMS_BATCH_LIMIT,
    on_chunk_done: Optional[callable] = None,
) -> Tuple[int, List[str]]:
    """Asocia N line items a un quote vía v4 batch.

    Devuelve (successful_count, errors). Una asociación fallida no aborta el resto.
    """
    url = "https://api.hubapi.com/crm/v4/associations/quotes/line_items/batch/create"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    association_type_id = AssociationType.QUOTE_TO_LINE_ITEM.value

    successful = 0
    errors: List[str] = []

    for chunk_start in range(0, len(line_item_ids), chunk_size):
        chunk = line_item_ids[chunk_start:chunk_start + chunk_size]
        payload = {
            "inputs": [
                {
                    "from": {"id": str(quote_id)},
                    "to": {"id": str(li_id)},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": association_type_id,
                        }
                    ],
                }
                for li_id in chunk
            ]
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
        except Exception as e:
            error_msg = f"Chunk [{chunk_start}:{chunk_start + len(chunk)}] exception: {e}"
            logger.error(f"[batch_associate_line_items_to_quote_v4] {error_msg}", exc_info=True)
            errors.append(error_msg)
            continue

        if response.status_code >= 300:
            error_msg = f"Chunk [{chunk_start}:{chunk_start + len(chunk)}] HTTP {response.status_code}: {response.text}"
            logger.error(f"[batch_associate_line_items_to_quote_v4] {error_msg}")
            errors.append(error_msg)
            continue

        try:
            results = response.json().get("results", [])
            successful += len(results)
            logger.info(
                f"[batch_associate_line_items_to_quote_v4] Chunk [{chunk_start}:{chunk_start + len(chunk)}] "
                f"associated {len(results)} line items to quote {quote_id}"
            )
        except Exception as e:
            error_msg = f"Chunk [{chunk_start}:{chunk_start + len(chunk)}] could not parse response: {e}"
            logger.error(f"[batch_associate_line_items_to_quote_v4] {error_msg}")
            errors.append(error_msg)
            continue

        if on_chunk_done:
            try:
                on_chunk_done(successful)
            except Exception as cb_err:
                logger.warning(f"[batch_associate_line_items_to_quote_v4] on_chunk_done failed: {cb_err}")

    return successful, errors