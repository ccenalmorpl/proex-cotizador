#!/bin/bash
# test-docker-build.sh - Test Docker build locally before deploying

set -e

echo "🧪 Testing Docker build locally..."

# Build the image
echo "📦 Building Docker image..."
docker build \
  --platform linux/amd64 \
  --no-cache \
  -t proex-cotizador-test:latest .

echo ""
echo "✅ Build completed successfully!"
echo ""
echo "🔍 Verifying app structure in the image..."

# Lambda images use a different entrypoint, so we need to override it for testing
# Run a test container to verify the structure
docker run --rm --platform linux/amd64 \
  --entrypoint /bin/sh \
  proex-cotizador-test:latest \
  -c "ls -la /var/task/app/ && echo '' && echo '📂 Utils directory:' && ls -la /var/task/app/utils/ && echo '' && echo '✅ All files present!'"

echo ""
echo "🧪 Testing if Python can import the module..."

docker run --rm --platform linux/amd64 \
  --entrypoint /bin/sh \
  proex-cotizador-test:latest \
  -c "cd /var/task && python -c 'from app.utils.property_helpers import safe_convert; print(\"✅ Import successful!\")'"

echo ""
echo "🎉 All tests passed! Ready to deploy."
