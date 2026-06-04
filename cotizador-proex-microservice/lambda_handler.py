from mangum import Mangum
from main import app

# api_gateway_base_path="/" para API Gateway HTTP API
handler = Mangum(app, lifespan="off", api_gateway_base_path="/")
