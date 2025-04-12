FROM mcr.microsoft.com/playwright/python:v1.51.0-jammy

# Create app folder
WORKDIR /app

# Copy files into image
COPY . .

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Default command
CMD ["python", "MVP_ALL_Headless_Railway.py"]
