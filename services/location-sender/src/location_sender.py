import json
import logging
import os
import sys
import time

import pika
from flask import Flask, jsonify, request

# Configure logging - use a single consistent method
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],  # Log to stdout explicitly
    force=True,  # Force configuration to override any existing loggers
)

# Get the root logger
logger = logging.getLogger()

# Create Flask app
app = Flask(__name__)

# Log startup message to verify logging works
logger.info("Starting location sender service")

# RabbitMQ setup
RABBITMQ_HOST = "rabbitmq" # os.environ.get("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = "gps_data"
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS", "guest")
RABBITMQ_RECONNECT_DELAY = 5  # seconds between reconnection attempts

logger.info("RabbitMQ Host: %s", RABBITMQ_HOST)
logger.info("Using queue: %s", RABBITMQ_QUEUE)

# Define global connection variables
connection = None
channel = None


def connect_to_rabbitmq():
    """Connect to RabbitMQ and return connection and channel"""
    global connection, channel

    try:
        logger.info("Attempting to connect to RabbitMQ...")
        print(f"Connecting to RabbitMQ at {RABBITMQ_HOST} with user {RABBITMQ_USER} and password {RABBITMQ_PASS}")
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                credentials=credentials,
                heartbeat=600,  # Increase heartbeat for better connection stability
                blocked_connection_timeout=300,
            )
        )
        channel = connection.channel()
        channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
        logger.info("Successfully connected to RabbitMQ")
        return True
    except Exception as e:
        logger.error("Failed to connect to RabbitMQ: %s", str(e))
        connection = None
        channel = None
        return False


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    rabbitmq_status = (
        "connected" if connection and connection.is_open else "disconnected"
    )
    return jsonify({"status": "ok", "rabbitmq": rabbitmq_status})


@app.route("/gps", methods=["POST"])
def receive_gps() -> json:
    """Receive GPS data and send to RabbitMQ"""
    global connection, channel

    logger.info("Received GPS data")
    try:
        # Validate incoming data
        data = request.get_json()
        if not data or "vehicle_id" not in data or "gps" not in data:
            logger.error("Invalid data received: %s", data)
            return jsonify({"error": "Invalid data"}), 400

        # Format message
        message = json.dumps(
            {
                "timestamp": data.get("timestamp", ""),
                "vehicle_id": data["vehicle_id"],
                "gps": data["gps"],
            }
        )

        # Check connection status and attempt to reconnect if needed
        rabbitmq_connected = False
        if connection is None or not connection.is_open:
            logger.warning(
                "RabbitMQ connection not available, attempting to reconnect")
            rabbitmq_connected = connect_to_rabbitmq()
        else:
            rabbitmq_connected = True

        # If we have a connection, publish the message
        if rabbitmq_connected:
            channel.basic_publish(
                exchange="",
                routing_key=RABBITMQ_QUEUE,
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Make message persistent
                    content_type="application/json",
                ),
            )
            logger.info("Sent GPS data to RabbitMQ: %s", message)
            return jsonify({"status": "sent"}), 200
        else:
            logger.error(
                "Failed to send message - RabbitMQ connection unavailable")
            return jsonify({"error": "Message queue unavailable"}), 503

    except Exception as e:
        logger.error("Error processing GPS data: %s", str(e), exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


# Flask debug and logging settings
if __name__ == "__main__":
    # Initial connection attempt
    connect_attempt = False
    while not connect_attempt:
        connect_attempt = connect_to_rabbitmq()
        if not connect_attempt:
            logger.warning(
                "Initial connection to RabbitMQ failed. Will retry in 5 seconds."
            )
            time.sleep(RABBITMQ_RECONNECT_DELAY)

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
