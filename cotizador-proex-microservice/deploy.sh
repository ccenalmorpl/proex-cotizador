#!/bin/bash
# deploy.sh - Script automatizado de deployment
#
# Este script despliega la función Lambda usando API Gateway HTTP API como método
# de exposición por defecto (en lugar de Lambda Function URL)

set -e  # Exit on error

# Cargar variables
source .env  # Archivo con AWS_REGION, FUNCTION_NAME, etc.

# AWS Profile (cambiar si usas otro perfil)
AWS_PROFILE=${AWS_PROFILE:-biatic}

echo "🚀 Iniciando deployment de ${FUNCTION_NAME}..."
echo "📌 Using AWS Profile: ${AWS_PROFILE}"

# Check if ECR repository exists, create if not
echo "🔍 Checking ECR repository..."
if ! aws ecr describe-repositories --repository-names ${ECR_REPO_NAME} --region ${AWS_REGION} --profile ${AWS_PROFILE} &>/dev/null; then
  echo "📦 Creating ECR repository ${ECR_REPO_NAME}..."
  aws ecr create-repository \
    --repository-name ${ECR_REPO_NAME} \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} \
    --image-scanning-configuration scanOnPush=true
  echo "✅ ECR repository created successfully"
else
  echo "✅ ECR repository already exists"
fi

# Set ECR repository policy to allow Lambda access
echo "🔐 Setting ECR repository policy..."
aws ecr set-repository-policy \
  --repository-name ${ECR_REPO_NAME} \
  --region ${AWS_REGION} \
  --profile ${AWS_PROFILE} \
  --policy-text "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Sid\": \"LambdaECRImageRetrievalPolicy\",
        \"Effect\": \"Allow\",
        \"Principal\": {
          \"Service\": \"lambda.amazonaws.com\"
        },
        \"Action\": [
          \"ecr:BatchGetImage\",
          \"ecr:GetDownloadUrlForLayer\"
        ]
      }
    ]
  }" || echo "⚠️  Warning: Could not set repository policy (may already exist)"

# Build
echo "📦 Building Docker image (with --no-cache to ensure fresh build)..."
docker build \
  --platform linux/amd64 \
  --provenance=false \
  --output type=docker \
  --no-cache \
  -t ${ECR_REPO_NAME}:latest .

# Tag
echo "🏷️  Tagging image..."
docker tag ${ECR_REPO_NAME}:latest \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest

# Login ECR
echo "🔐 Logging into ECR..."
aws ecr get-login-password --region ${AWS_REGION} --profile ${AWS_PROFILE} | \
  docker login --username AWS --password-stdin \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

# Push
echo "⬆️  Pushing to ECR..."
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest

# Check if Lambda function exists
echo "🔍 Checking Lambda function..."
if ! aws lambda get-function --function-name ${FUNCTION_NAME} --region ${AWS_REGION} --profile ${AWS_PROFILE} &>/dev/null; then
  echo "📦 Creating Lambda function ${FUNCTION_NAME}..."

  # Create Lambda execution role if it doesn't exist
  ROLE_NAME="${FUNCTION_NAME}-role"
  ROLE_ARN=$(aws iam get-role --role-name ${ROLE_NAME} --profile ${AWS_PROFILE} --query 'Role.Arn' --output text 2>/dev/null || echo "")

  if [ -z "$ROLE_ARN" ]; then
    echo "📝 Creating IAM role ${ROLE_NAME}..."
    aws iam create-role \
      --role-name ${ROLE_NAME} \
      --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
          "Effect": "Allow",
          "Principal": {"Service": "lambda.amazonaws.com"},
          "Action": "sts:AssumeRole"
        }]
      }' \
      --region ${AWS_REGION} \
      --profile ${AWS_PROFILE}

    # Attach basic Lambda execution policy
    aws iam attach-role-policy \
      --role-name ${ROLE_NAME} \
      --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
      --profile ${AWS_PROFILE}

    # Create and attach ECR access policy
    echo "📝 Creating ECR access policy..."
    ECR_POLICY_NAME="${FUNCTION_NAME}-ecr-policy"
    aws iam create-policy \
      --policy-name ${ECR_POLICY_NAME} \
      --profile ${AWS_PROFILE} \
      --policy-document "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [
          {
            \"Effect\": \"Allow\",
            \"Action\": [
              \"ecr:GetAuthorizationToken\",
              \"ecr:BatchCheckLayerAvailability\",
              \"ecr:GetDownloadUrlForLayer\",
              \"ecr:BatchGetImage\"
            ],
            \"Resource\": \"*\"
          }
        ]
      }" 2>/dev/null || echo "⚠️  Policy may already exist"

    # Attach ECR policy
    aws iam attach-role-policy \
      --role-name ${ROLE_NAME} \
      --policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${ECR_POLICY_NAME} \
      --profile ${AWS_PROFILE} 2>/dev/null || \
      echo "⚠️  Could not attach ECR policy (may already be attached)"

    # Wait for role to be ready
    echo "⏳ Waiting for IAM role to be ready..."
    sleep 15

    ROLE_ARN=$(aws iam get-role --role-name ${ROLE_NAME} --profile ${AWS_PROFILE} --query 'Role.Arn' --output text)
  fi

  # Create Lambda function
  aws lambda create-function \
    --function-name ${FUNCTION_NAME} \
    --package-type Image \
    --code ImageUri=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest \
    --role ${ROLE_ARN} \
    --timeout 30 \
    --memory-size 512 \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} \
    --environment "Variables={PROEX_ACCESS_TOKEN=${PROEX_ACCESS_TOKEN},CORS_ORIGINS=\"${CORS_ORIGINS}\"}"

  echo "✅ Lambda function created successfully"

  # Create API Gateway HTTP API
  echo "🔗 Creating API Gateway HTTP API..."
  API_RESPONSE=$(aws apigatewayv2 create-api \
    --name ${FUNCTION_NAME}-api \
    --protocol-type HTTP \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE})

  API_ID=$(echo $API_RESPONSE | grep -o '"ApiId":"[^"]*"' | cut -d'"' -f4)
  echo "✅ API Gateway created with ID: ${API_ID}"

  # Create integration
  echo "🔗 Creating Lambda integration..."
  INTEGRATION_RESPONSE=$(aws apigatewayv2 create-integration \
    --api-id ${API_ID} \
    --integration-type AWS_PROXY \
    --integration-uri arn:aws:lambda:${AWS_REGION}:${AWS_ACCOUNT_ID}:function:${FUNCTION_NAME} \
    --payload-format-version 2.0 \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE})

  INTEGRATION_ID=$(echo $INTEGRATION_RESPONSE | grep -o '"IntegrationId":"[^"]*"' | cut -d'"' -f4)
  echo "✅ Integration created with ID: ${INTEGRATION_ID}"

  # Create default route
  echo "🔗 Creating default route..."
  aws apigatewayv2 create-route \
    --api-id ${API_ID} \
    --route-key '$default' \
    --target "integrations/${INTEGRATION_ID}" \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} > /dev/null
  echo "✅ Route created"

  # Create stage with auto-deploy
  echo "🔗 Creating stage..."
  aws apigatewayv2 create-stage \
    --api-id ${API_ID} \
    --stage-name '$default' \
    --auto-deploy \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} > /dev/null
  echo "✅ Stage created"

  # Configure CORS
  echo "🔗 Configuring CORS..."
  aws apigatewayv2 update-api \
    --api-id ${API_ID} \
    --cors-configuration 'AllowOrigins=*,AllowMethods=*,AllowHeaders=*' \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} > /dev/null
  echo "✅ CORS configured"

  # Add API Gateway invoke permission
  aws lambda add-permission \
    --function-name ${FUNCTION_NAME} \
    --statement-id apigateway-invoke \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${AWS_REGION}:${AWS_ACCOUNT_ID}:${API_ID}/*" \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} 2>/dev/null || echo "⚠️  Permission may already exist"
else
  echo "✅ Lambda function already exists, updating..."

  # Update Lambda
  aws lambda update-function-code \
    --function-name ${FUNCTION_NAME} \
    --image-uri ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}
fi

# Wait for update
echo "⏳ Waiting for update to complete..."
aws lambda wait function-updated \
  --function-name ${FUNCTION_NAME} \
  --region ${AWS_REGION} \
  --profile ${AWS_PROFILE}

# Get URL (try API Gateway first, then Function URL as fallback)
API_ID=$(aws apigatewayv2 get-apis \
  --region ${AWS_REGION} \
  --profile ${AWS_PROFILE} \
  --query "Items[?Name=='${FUNCTION_NAME}-api'].ApiId" \
  --output text 2>/dev/null)

if [ -n "$API_ID" ] && [ "$API_ID" != "None" ]; then
  LAMBDA_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com"
else
  # Fallback to Function URL if exists
  LAMBDA_URL=$(aws lambda get-function-url-config \
    --function-name ${FUNCTION_NAME} \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} \
    --query 'FunctionUrl' --output text 2>/dev/null)
fi

echo "✅ Deployment successful!"
echo "📍 URL: ${LAMBDA_URL}"
echo "📊 Logs: aws logs tail /aws/lambda/${FUNCTION_NAME} --follow --profile ${AWS_PROFILE}"
