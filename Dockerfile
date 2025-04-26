FROM mcr.microsoft.com/playwright/python:v1.51.0-jammy

# Create app folder
WORKDIR /app

# Copy files into image
COPY . .

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Install Playwright browsers (already installed usually, but safer to force reinstall)
RUN playwright install --with-deps

# Expose port (optional for Railway — just in case you later add webhooks, etc.)
EXPOSE 8000

# Default command
CMD ["python", "MVP_ALL_Railway.py"]
