from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.models.schemas import HealthResponse
from app.routes import deals, articles, templates
from datetime import datetime
import uvicorn
import logging

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format=settings.log_format
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="Microservice for Proex quote generation",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Include routers
app.include_router(deals.router, prefix="/api/v1/deals", tags=["deals"])
logger.info("Deals router registered at /deals")

app.include_router(articles.router, prefix="/api/v1/articles", tags=["articles"])
logger.info("Articles router registered at /articles")

app.include_router(articles.router, prefix="/api/v1/articles", tags=["articles"])
logger.info("Articles router registered at /articles")

app.include_router(templates.router, prefix="/api/v1/templates", tags=["templates"])
logger.info("Templates router registered at /templates")


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        version=settings.version,
        timestamp=datetime.now()
    )


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": f"Welcome to {settings.app_name}",
        "version": settings.version,
        "docs": "/docs"
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )
