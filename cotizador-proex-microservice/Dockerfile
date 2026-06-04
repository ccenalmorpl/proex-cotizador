# Dockerfile
FROM public.ecr.aws/lambda/python:3.11

# Copiar requirements e instalar
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --timeout 120 -r requirements.txt

# Copiar código de la app (asegurarse de incluir todos los subdirectorios)
COPY app/ ${LAMBDA_TASK_ROOT}/app/
COPY main.py ${LAMBDA_TASK_ROOT}/
COPY lambda_handler.py ${LAMBDA_TASK_ROOT}/
COPY worker_handler.py ${LAMBDA_TASK_ROOT}/

# Fix permissions on all app directories (importante para Lambda)
RUN chmod -R 755 ${LAMBDA_TASK_ROOT}/app/

# Verificar estructura de directorios
RUN ls -la ${LAMBDA_TASK_ROOT}/app/ && \
    ls -la ${LAMBDA_TASK_ROOT}/app/services/ || echo "WARNING: services directory not found"

# Handler de Lambda
CMD ["lambda_handler.handler"]
