FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV CDK_PLUS_DB_PATH=/app/data/cdk_plus.sqlite3
ENV CDK_PLUS_CLOUDMAIL_CONFIG=/app/config/cloudmail.config.json

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY cdk_plus/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY cdk_plus ./cdk_plus

RUN mkdir -p /app/data /app/config && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "cdk_plus.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
