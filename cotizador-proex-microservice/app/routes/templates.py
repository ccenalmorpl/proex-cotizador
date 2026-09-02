from fastapi import APIRouter, HTTPException
from app.services.hubspot_client import hubspot_client
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

class QuoteTemplate(BaseModel):
    id: str
    name: str
    is_default: bool = False

class TemplatesResponse(BaseModel):
    success: bool
    templates: list[QuoteTemplate] = []
    error: str = None

@router.get("", response_model=TemplatesResponse)
async def get_quote_templates():
    """
    Get all available quote templates from HubSpot
    """
    try:
        client = hubspot_client.get_client()

        logger.info('🔍 Fetching quote templates...')

        templates_response = client.crm.objects.basic_api.get_page(
            object_type='quote_template',
            limit=100,
            properties=['hs_name', 'hs_template_type', 'hs_is_default']
        )

        if not templates_response.results:
            logger.warning('No templates found')
            return TemplatesResponse(
                success=True,
                templates=[]
            )

        # Transform to response format
        templates = []
        for template in templates_response.results:
            templates.append(QuoteTemplate(
                id=template.id,
                name=template.properties.get('hs_name', 'Sin nombre'),
                is_default=template.properties.get('hs_is_default') == 'true'
            ))

        logger.info(f'✅ Found {len(templates)} templates')

        return TemplatesResponse(
            success=True,
            templates=templates
        )

    except Exception as e:
        logger.error(f"Error fetching templates: {e}")
        return TemplatesResponse(
            success=False,
            error=str(e)
        )
