FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    ML_MODELS_DIR=/app/models

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY pyproject.toml README.md /app/
COPY plc500_ml /app/plc500_ml
COPY models /app/models

RUN pip install --no-cache-dir --no-deps -e /app

EXPOSE 8000 502 8502 8001

CMD ["uvicorn", "plc500_ml.api:app", "--host", "0.0.0.0", "--port", "8000"]
