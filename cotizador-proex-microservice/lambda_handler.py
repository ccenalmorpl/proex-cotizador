# lambda_handler.py
from mangum import Mangum
from main import app

# Mangum adapta FastAPI para AWS Lambda
# api_gateway_base_path debe ser "/" para API Gateway HTTP API
handler = Mangum(app, lifespan="off", api_gateway_base_path="/")
