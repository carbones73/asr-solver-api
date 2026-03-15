FROM python:3.11-slim

WORKDIR /app

# System dependencies for PDF rendering (PyMuPDF)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .
COPY extractor.py .
COPY employees.py .
COPY batch_upload.py .
COPY gemini_solver.py .

# Cloud Run uses PORT env variable (default 8080)
ENV PORT=8080

EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
