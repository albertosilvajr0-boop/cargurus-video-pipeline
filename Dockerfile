FROM python:3.12-slim

# Install system dependencies (ffmpeg for video processing, fonts for overlays)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create output directories
RUN mkdir -p output/photos output/stickers output/scripts output/videos output/uploads output/branding

# Cloud Run uses PORT env variable
ENV PORT=8080

EXPOSE 8080

# Run with gunicorn for production
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 300 app:app
