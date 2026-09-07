#!/bin/bash
# deploy.sh - Script automatizado de deployment

set -e

source .env

AWS_PROFILE=${AWS_PROFILE:-biatic}
JOBS_ACCESS_POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/proex-jobs-access"

echo "🚀 Iniciando deployment de ${FUNCTION_NAME}..."
echo "📌 Using AWS Profile: ${AWS_PROFILE}"

ensure_proex_jobs_access_policy() {
  if aws iam get-policy --policy-arn "${JOBS_ACCESS_POLICY_ARN}" --profile ${AWS_PROFILE} &>/dev/null; then
    return 0
  fi
  echo "📝 Creando IAM policy proex-jobs-access (no existía)..."
  local tmp
  tmp=$(mktemp)
  cat > "$tmp" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDBJobsAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:GetItem",
        "dynamodb:Query"
      ],
      "Resource": [
        "arn:aws:dynamodb:${AWS_REGION}:${AWS_ACCOUNT_ID}:table/${JOBS_TABLE_NAME}",
        "arn:aws:dynamodb:${AWS_REGION}:${AWS_ACCOUNT_ID}:table/${JOBS_TABLE_NAME}/index/*"
      ]
    },
    {
      "Sid": "SQSJobsSend",
      "Effect": "Allow",
      "Action": ["sqs:SendMessage"],
      "Resource": "${JOBS_QUEUE_ARN}"
    }
  ]
}
EOF
  aws iam create-policy \
    --policy-name proex-jobs-access \
    --policy-document "file://$tmp" \
    --profile ${AWS_PROFILE}
  rm -f "$tmp"
}

attach_proex_jobs_access_policy() {
  local role="$1"
  ensure_proex_jobs_access_policy
  aws iam attach-role-policy \
    --role-name "$role" \
    --policy-arn "${JOBS_ACCESS_POLICY_ARN}" \
    --profile ${AWS_PROFILE}
  echo "✅ proex-jobs-access attached a $role"
}

# ECR repository
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

# Lifecycle policy: conservar solo las 10 imagenes sin etiqueta mas recientes (nunca toca 'latest')
echo "🧹 Aplicando lifecycle policy ECR (conservar 10 untagged)..."
aws ecr put-lifecycle-policy \
  --repository-name ${ECR_REPO_NAME} \
  --region ${AWS_REGION} \
  --profile ${AWS_PROFILE} \
  --lifecycle-policy-text '{"rules":[{"rulePriority":1,"description":"Conservar las 10 imagenes sin etiqueta mas recientes; expirar el resto. Nunca toca imagenes etiquetadas (latest).","selection":{"tagStatus":"untagged","countType":"imageCountMoreThan","countNumber":10},"action":{"type":"expire"}}]}' \
  >/dev/null && echo "✅ Lifecycle policy aplicada"

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

echo "🏷️  Tagging image..."
docker tag ${ECR_REPO_NAME}:latest \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest

echo "🔐 Logging into ECR..."
aws ecr get-login-password --region ${AWS_REGION} --profile ${AWS_PROFILE} | \
  docker login --username AWS --password-stdin \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

echo "⬆️  Pushing to ECR..."
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest

ROLE_NAME="${FUNCTION_NAME}-role"
if aws iam get-role --role-name ${ROLE_NAME} --profile ${AWS_PROFILE} &>/dev/null; then
  attach_proex_jobs_access_policy ${ROLE_NAME}
fi

echo "🔍 Checking Lambda function..."
if ! aws lambda get-function --function-name ${FUNCTION_NAME} --region ${AWS_REGION} --profile ${AWS_PROFILE} &>/dev/null; then
  echo "📦 Creating Lambda function ${FUNCTION_NAME}..."

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

    aws iam attach-role-policy \
      --role-name ${ROLE_NAME} \
      --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
      --profile ${AWS_PROFILE}

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

    aws iam attach-role-policy \
      --role-name ${ROLE_NAME} \
      --policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${ECR_POLICY_NAME} \
      --profile ${AWS_PROFILE} 2>/dev/null || \
      echo "⚠️  Could not attach ECR policy (may already be attached)"

    attach_proex_jobs_access_policy ${ROLE_NAME}

    echo "⏳ Waiting for IAM role to be ready..."
    sleep 15

    ROLE_ARN=$(aws iam get-role --role-name ${ROLE_NAME} --profile ${AWS_PROFILE} --query 'Role.Arn' --output text)
  fi

  aws lambda create-function \
    --function-name ${FUNCTION_NAME} \
    --package-type Image \
    --code ImageUri=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest \
    --role ${ROLE_ARN} \
    --timeout 30 \
    --memory-size 512 \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} \
    --environment "Variables={
      PROEX_ACCESS_TOKEN=${PROEX_ACCESS_TOKEN},
      PROEX_CARD_SECRET=${PROEX_CARD_SECRET},
      CORS_ORIGINS=\"${CORS_ORIGINS}\",
      JOBS_TABLE_NAME=${JOBS_TABLE_NAME},
      JOBS_QUEUE_URL=${JOBS_QUEUE_URL}
    }"

  echo "✅ Lambda function created successfully"

  echo "🔗 Creating API Gateway HTTP API..."
  API_RESPONSE=$(aws apigatewayv2 create-api \
    --name ${FUNCTION_NAME}-api \
    --protocol-type HTTP \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE})

  API_ID=$(echo $API_RESPONSE | grep -o '"ApiId":"[^"]*"' | cut -d'"' -f4)
  echo "✅ API Gateway created with ID: ${API_ID}"

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

  echo "🔗 Creating default route..."
  aws apigatewayv2 create-route \
    --api-id ${API_ID} \
    --route-key '$default' \
    --target "integrations/${INTEGRATION_ID}" \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} > /dev/null
  echo "✅ Route created"

  echo "🔗 Creating stage..."
  aws apigatewayv2 create-stage \
    --api-id ${API_ID} \
    --stage-name '$default' \
    --auto-deploy \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} > /dev/null
  echo "✅ Stage created"

  echo "🔗 Configuring CORS..."
  aws apigatewayv2 update-api \
    --api-id ${API_ID} \
    --cors-configuration 'AllowOrigins=*,AllowMethods=*,AllowHeaders=*' \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} > /dev/null
  echo "✅ CORS configured"

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

  aws lambda update-function-code \
    --function-name ${FUNCTION_NAME} \
    --image-uri ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}

  echo "⏳ Waiting for code update..."
  aws lambda wait function-updated \
    --function-name ${FUNCTION_NAME} \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}

  echo "🔧 Updating environment variables..."
  aws lambda update-function-configuration \
    --function-name ${FUNCTION_NAME} \
    --environment "Variables={
      PROEX_ACCESS_TOKEN=${PROEX_ACCESS_TOKEN},
      PROEX_CARD_SECRET=${PROEX_CARD_SECRET},
      CORS_ORIGINS=\"${CORS_ORIGINS}\",
      JOBS_TABLE_NAME=${JOBS_TABLE_NAME},
      JOBS_QUEUE_URL=${JOBS_QUEUE_URL}
    }" \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}
fi

echo "⏳ Waiting for update to complete..."
aws lambda wait function-updated \
  --function-name ${FUNCTION_NAME} \
  --region ${AWS_REGION} \
  --profile ${AWS_PROFILE}

# proex-jobs-worker corre esta misma imagen (publicación de custom objects, re-sync
# de Contenedor/Elemento). Lambda fija el digest al desplegar, así que sin este
# update se queda con el código anterior aunque el tag siga siendo :latest.
WORKER_NAME="proex-jobs-worker"
if aws lambda get-function --function-name ${WORKER_NAME} --region ${AWS_REGION} --profile ${AWS_PROFILE} &>/dev/null; then
  echo "🔄 Actualizando ${WORKER_NAME} con la misma imagen..."
  aws lambda update-function-code \
    --function-name ${WORKER_NAME} \
    --image-uri ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} > /dev/null

  aws lambda wait function-updated \
    --function-name ${WORKER_NAME} \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}
  echo "✅ ${WORKER_NAME} actualizado"
else
  echo "⚠️  ${WORKER_NAME} no existe; se omite"
fi

API_ID=$(aws apigatewayv2 get-apis \
  --region ${AWS_REGION} \
  --profile ${AWS_PROFILE} \
  --query "Items[?Name=='${FUNCTION_NAME}-api'].ApiId" \
  --output text 2>/dev/null)

if [ -n "$API_ID" ] && [ "$API_ID" != "None" ]; then
  LAMBDA_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com"
else
  LAMBDA_URL=$(aws lambda get-function-url-config \
    --function-name ${FUNCTION_NAME} \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} \
    --query 'FunctionUrl' --output text 2>/dev/null)
fi

echo "✅ Deployment successful!"
echo "📍 URL: ${LAMBDA_URL}"
echo "📊 Logs: aws logs tail /aws/lambda/${FUNCTION_NAME} --follow --profile ${AWS_PROFILE}"