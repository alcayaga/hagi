FROM python:3.12-slim

# Install ffmpeg for media extraction
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the default UI port
EXPOSE 8000

# Set the default command to start the web UI
CMD ["python", "cli.py", "ui", "--host", "0.0.0.0", "--port", "8000"]
