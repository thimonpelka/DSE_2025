import json
import os
from flask import Flask, request, jsonify
import pika
import logging
import sys

# Configure logging - use a single consistent method
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)  # Log to stdout explicitly
    ],
)

# Get the root logger
logger = logging.getLogger()

# Create Flask app
app = Flask(__name__)

# Log startup message to verify logging works
logger.info("Starting location sender service")

# RabbitMQ setup
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = "gps_data"
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS", "guest")

logger.info("RabbitMQ Host: %s", RABBITMQ_HOST)
logger.info("Authenticating with credentials %s:%s",
            RABBITMQ_USER, RABBITMQ_PASS)

# Connect to RabbitMQ
try:
    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
    )
    channel = connection.channel()
    channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
    logger.info("Successfully connected to RabbitMQ")
except Exception as e:
    logger.error("Failed to connect to RabbitMQ: %s", str(e))
    # Don't crash the app, we'll handle reconnection later


@app.route("/gps", methods=["POST"])
def receive_gps() -> json:
    logger.info("Received GPS data")

    try:
        data = request.get_json()
        if not data or "vehicle_id" not in data or "gps" not in data:
            logger.error("Invalid data received: %s", data)
            return jsonify({"error": "Invalid data"}), 400

        message = json.dumps(
            {
                "timestamp": data.get("timestamp", ""),
                "vehicle_id": data["vehicle_id"],
                "gps": data["gps"],
            }
        )

        # Check if connection is open, reconnect if needed
        if not connection.is_open:
            logger.warning(
                "RabbitMQ connection closed, attempting to reconnect")
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST, credentials=credentials)
            )
            channel = connection.channel()
            channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)

        channel.basic_publish(
            exchange="",
            routing_key=RABBITMQ_QUEUE,
            body=message,
            properties=pika.BasicProperties(delivery_mode=2),
        )

        logger.info("Sent GPS data to RabbitMQ: %s", message)
        return jsonify({"status": "sent"}), 200

    except Exception as e:
        logger.error("Error processing GPS data: %s", str(e), exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


# Flask debug and logging settings
if __name__ == "__main__":
    # Make sure Flask doesn't suppress logs
    app.logger.handlers = logger.handlers
    app.logger.setLevel(logger.level)

    # Enable stdout/stderr flushing
    sys.stdout.flush()
    sys.stderr.flush()

    # Run the app
    logger.info("Starting Flask application on 0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
else:
    # When run by a WSGI server, ensure logging is properly set up
    gunicorn_logger = logging.getLogger("gunicorn.error")
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
    logger.info("Application started via WSGI")
