FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ .

RUN mkdir -p /data
VOLUME ["/data"]

RUN python -m pytest tests/

CMD ["python", "location_tracker.py"]
