#!/bin/bash
# deploy-worker.sh - Despliega la Lambda worker que consume SQS y ejecuta jobs.
# Reutiliza la misma imagen ECR que la API (override de CMD vía ImageConfig).

set -e

source .env

AWS_PROFILE=${AWS_PROFILE:-biatic}
WORKER_FUNCTION_NAME=${WORKER_FUNCTION_NAME:-proex-jobs-worker}
WORKER_ROLE_NAME="${WORKER_FUNCTION_NAME}-role"
WORKER_TIMEOUT=${WORKER_TIMEOUT:-90}
WORKER_MEMORY=${WORKER_MEMORY:-512}
IMAGE_URI=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest
JOBS_ACCESS_POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/proex-jobs-access"

echo "🚀 Iniciando deployment del worker ${WORKER_FUNCTION_NAME}..."
echo "📌 Using AWS Profile: ${AWS_PROFILE}"
echo "🖼️  Image URI: ${IMAGE_URI}"

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

if [ -z "${JOBS_QUEUE_ARN}" ]; then
  echo "❌ JOBS_QUEUE_ARN no definido en .env"
  exit 1
fi

if ! aws ecr describe-images \
    --repository-name ${ECR_REPO_NAME} \
    --image-ids imageTag=latest \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} &>/dev/null; then
  echo "❌ La imagen ${IMAGE_URI} no existe. Ejecuta ./deploy.sh antes para publicarla."
  exit 1
fi

echo "🔍 Verificando rol IAM del worker..."
WORKER_ROLE_ARN=$(aws iam get-role --role-name ${WORKER_ROLE_NAME} --profile ${AWS_PROFILE} --query 'Role.Arn' --output text 2>/dev/null || echo "")

if [ -z "$WORKER_ROLE_ARN" ]; then
  echo "📝 Creando rol IAM ${WORKER_ROLE_NAME}..."
  aws iam create-role \
    --role-name ${WORKER_ROLE_NAME} \
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
    --role-name ${WORKER_ROLE_NAME} \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
    --profile ${AWS_PROFILE}

  ECR_POLICY_NAME="${FUNCTION_NAME}-ecr-policy"
  aws iam attach-role-policy \
    --role-name ${WORKER_ROLE_NAME} \
    --policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${ECR_POLICY_NAME} \
    --profile ${AWS_PROFILE} 2>/dev/null || echo "⚠️  ECR policy may already be attached"

  attach_proex_jobs_access_policy ${WORKER_ROLE_NAME}

  WORKER_SQS_POLICY_NAME="${WORKER_FUNCTION_NAME}-sqs-consume"
  aws iam create-policy \
    --policy-name ${WORKER_SQS_POLICY_NAME} \
    --profile ${AWS_PROFILE} \
    --policy-document "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [
        {
          \"Effect\": \"Allow\",
          \"Action\": [
            \"sqs:ReceiveMessage\",
            \"sqs:DeleteMessage\",
            \"sqs:GetQueueAttributes\",
            \"sqs:ChangeMessageVisibility\"
          ],
          \"Resource\": \"${JOBS_QUEUE_ARN}\"
        }
      ]
    }" 2>/dev/null || echo "⚠️  SQS consume policy may already exist"

  aws iam attach-role-policy \
    --role-name ${WORKER_ROLE_NAME} \
    --policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${WORKER_SQS_POLICY_NAME} \
    --profile ${AWS_PROFILE} 2>/dev/null || echo "⚠️  SQS consume policy may already be attached"

  echo "⏳ Esperando propagación del rol IAM..."
  sleep 15

  WORKER_ROLE_ARN=$(aws iam get-role --role-name ${WORKER_ROLE_NAME} --profile ${AWS_PROFILE} --query 'Role.Arn' --output text)
  echo "✅ Rol creado: ${WORKER_ROLE_ARN}"
else
  echo "✅ Rol ${WORKER_ROLE_NAME} ya existe: ${WORKER_ROLE_ARN}"
fi

echo "🔍 Verificando Lambda worker..."
if ! aws lambda get-function --function-name ${WORKER_FUNCTION_NAME} --region ${AWS_REGION} --profile ${AWS_PROFILE} &>/dev/null; then
  echo "📦 Creando Lambda ${WORKER_FUNCTION_NAME}..."
  aws lambda create-function \
    --function-name ${WORKER_FUNCTION_NAME} \
    --package-type Image \
    --code ImageUri=${IMAGE_URI} \
    --image-config '{"Command":["worker_handler.handler"]}' \
    --role ${WORKER_ROLE_ARN} \
    --timeout ${WORKER_TIMEOUT} \
    --memory-size ${WORKER_MEMORY} \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE} \
    --environment "Variables={
      PROEX_ACCESS_TOKEN=${PROEX_ACCESS_TOKEN},
      JOBS_TABLE_NAME=${JOBS_TABLE_NAME},
      JOBS_QUEUE_URL=${JOBS_QUEUE_URL}
    }"
  echo "✅ Lambda worker creada"
else
  echo "✅ Lambda worker existe, actualizando..."
  aws lambda update-function-code \
    --function-name ${WORKER_FUNCTION_NAME} \
    --image-uri ${IMAGE_URI} \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}

  aws lambda wait function-updated \
    --function-name ${WORKER_FUNCTION_NAME} \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}

  aws lambda update-function-configuration \
    --function-name ${WORKER_FUNCTION_NAME} \
    --timeout ${WORKER_TIMEOUT} \
    --memory-size ${WORKER_MEMORY} \
    --image-config '{"Command":["worker_handler.handler"]}' \
    --environment "Variables={
      PROEX_ACCESS_TOKEN=${PROEX_ACCESS_TOKEN},
      JOBS_TABLE_NAME=${JOBS_TABLE_NAME},
      JOBS_QUEUE_URL=${JOBS_QUEUE_URL}
    }" \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}
fi

aws lambda wait function-updated \
  --function-name ${WORKER_FUNCTION_NAME} \
  --region ${AWS_REGION} \
  --profile ${AWS_PROFILE}

echo "🔗 Verificando event source mapping SQS → worker..."
EXISTING_MAPPING_UUID=$(aws lambda list-event-source-mappings \
  --function-name ${WORKER_FUNCTION_NAME} \
  --event-source-arn ${JOBS_QUEUE_ARN} \
  --region ${AWS_REGION} \
  --profile ${AWS_PROFILE} \
  --query 'EventSourceMappings[0].UUID' \
  --output text 2>/dev/null)

if [ -z "$EXISTING_MAPPING_UUID" ] || [ "$EXISTING_MAPPING_UUID" == "None" ]; then
  echo "📝 Creando event source mapping..."
  aws lambda create-event-source-mapping \
    --function-name ${WORKER_FUNCTION_NAME} \
    --event-source-arn ${JOBS_QUEUE_ARN} \
    --batch-size 1 \
    --region ${AWS_REGION} \
    --profile ${AWS_PROFILE}
  echo "✅ Event source mapping creado"
else
  echo "✅ Event source mapping ya existe: ${EXISTING_MAPPING_UUID}"
fi

echo "✅ Deployment del worker completado"
echo "📊 Logs: aws logs tail /aws/lambda/${WORKER_FUNCTION_NAME} --follow --profile ${AWS_PROFILE} --region ${AWS_REGION}"
