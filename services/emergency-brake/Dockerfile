FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ .

RUN python -m pytest tests/

EXPOSE 5000
CMD ["python", "emergency_brake.py"]
