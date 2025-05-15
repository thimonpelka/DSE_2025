import json
import os
import time
import threading
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request
import pika
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)

# Get the root logger
logger = logging.getLogger()

# Create Flask app
app = Flask(__name__)

# Database setup
DB_PATH = os.environ.get("DB_PATH", "/data/gps.db")
logger.info(f"Using database at: {DB_PATH}")


def init_db():
    """Initialize the SQLite database"""
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Create table if it doesn't exist
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gps_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            timestamp TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        # Create index on vehicle_id for faster lookups
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_vehicle_id ON gps_data(vehicle_id)
        """)

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Database initialization error: {str(e)}", exc_info=True)
        return False


# RabbitMQ setup
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = "gps_data"
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS", "guest")
RABBITMQ_RECONNECT_DELAY = 5  # seconds between reconnection attempts

logger.info(f"RabbitMQ Host: {RABBITMQ_HOST}")
logger.info(f"Using queue: {RABBITMQ_QUEUE}")


def store_gps_data(data):
    """Store GPS data in the database"""
    try:
        vehicle_id = data.get("vehicle_id")
        gps = data.get("gps", {})
        latitude = gps.get("latitude")
        longitude = gps.get("longitude")
        timestamp = data.get("timestamp")

        # Validate required data
        if not all([vehicle_id, latitude is not None, longitude is not None]):
            logger.error(f"Invalid GPS data: {data}")
            return False

        # Use current timestamp if none provided
        if not timestamp:
            timestamp = datetime.utcnow().isoformat()

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO gps_data (vehicle_id, latitude, longitude, timestamp, created_at) VALUES (?, ?, ?, ?, ?)",
            (vehicle_id, latitude, longitude,
             timestamp, datetime.utcnow().isoformat()),
        )

        conn.commit()
        conn.close()
        logger.info(
            f"Stored GPS data for vehicle {vehicle_id}: {latitude}, {longitude}"
        )
        return True
    except Exception as e:
        logger.error(f"Error storing GPS data: {str(e)}", exc_info=True)
        return False


def rabbitmq_consumer():
    """Background thread to consume messages from RabbitMQ"""
    while True:
        try:
            # Connect to RabbitMQ
            logger.info("Connecting to RabbitMQ...")
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    credentials=credentials,
                    heartbeat=600,
                    blocked_connection_timeout=300,
                )
            )
            channel = connection.channel()

            # Declare queue
            channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)

            # Define callback for message processing
            def callback(ch, method, properties, body):
                try:
                    logger.info(f"Received message: {body}")
                    data = json.loads(body)
                    if store_gps_data(data):
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                    else:
                        # Negative acknowledgment if processing failed
                        # This will requeue the message
                        logger.warning("Failed to process message, nacking")
                        ch.basic_nack(
                            delivery_tag=method.delivery_tag, requeue=True)
                except Exception as e:
                    logger.error(f"Error processing message: {str(e)}", exc_info=True)
                    # Nack on exception
                    ch.basic_nack(
                        delivery_tag=method.delivery_tag, requeue=True)

            # Set prefetch count to control number of unacknowledged messages
            channel.basic_qos(prefetch_count=1)

            # Start consuming
            channel.basic_consume(queue=RABBITMQ_QUEUE,
                                  on_message_callback=callback)

            logger.info("Connected to RabbitMQ, waiting for messages...")
            channel.start_consuming()

        except Exception as e:
            logger.error(f"RabbitMQ consumer error: {str(e)}", exc_info=True)
            logger.info(f"Retrying in {RABBITMQ_RECONNECT_DELAY} seconds...")
            time.sleep(RABBITMQ_RECONNECT_DELAY)


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    try:
        # Verify database connection
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM gps_data")
        count = cursor.fetchone()[0]
        conn.close()

        return jsonify({"status": "ok", "database": "connected", "record_count": count})
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        return jsonify({"status": "error", "database": "error", "message": str(e)}), 500


@app.route("/api/vehicle/<vehicle_id>/location", methods=["GET"])
def get_vehicle_location(vehicle_id):
    """Get the latest GPS location for a specific vehicle"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row  # Return rows as dictionaries
        cursor = conn.cursor()

        # Get latest location for the vehicle
        cursor.execute(
            "SELECT vehicle_id, latitude, longitude, timestamp FROM gps_data WHERE vehicle_id = ? ORDER BY id DESC LIMIT 1",
            (vehicle_id,),
        )

        result = cursor.fetchone()
        conn.close()

        if result:
            return jsonify(
                {
                    "vehicle_id": result["vehicle_id"],
                    "gps": {
                        "latitude": result["latitude"],
                        "longitude": result["longitude"],
                    },
                    "timestamp": result["timestamp"],
                }
            )
        else:
            return jsonify(
                {"error": f"No location data found for vehicle {vehicle_id}"}
            ), 404

    except Exception as e:
        logger.error(f"Error retrieving vehicle location: {str(e)}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


# Initialize the application
if __name__ == "__main__":
    # Initialize database
    if not init_db():
        logger.error("Failed to initialize database. Exiting.")
        sys.exit(1)

    # Start RabbitMQ consumer in a separate thread
    consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
    consumer_thread.start()

    # Enable stdout/stderr flushing
    sys.stdout.flush()
    sys.stderr.flush()

    # Run Flask application
    logger.info("Starting Flask application on 0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
else:
    # When run by a WSGI server
    gunicorn_logger = logging.getLogger("gunicorn.error")
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

    # Initialize database
    init_db()

    # Start RabbitMQ consumer in a separate thread
    consumer_thread = threading.Thread(target=rabbitmq_consumer, daemon=True)
    consumer_thread.start()

    logger.info("Application started via WSGI")
