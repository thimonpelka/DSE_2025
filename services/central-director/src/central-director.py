import os
import threading
import json
import time
import sqlite3
import logging
import sys
from datetime import datetime
from flask import Flask, request, jsonify
import pika

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logging.getLogger("pika").setLevel(logging.INFO)
logger = logging.getLogger()

# Configuration via environment variables
DB_PATH = os.getenv("DB_PATH", "/data/cd_events.db")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_DM_QUEUE = "distance_data"
RABBITMQ_EB_QUEUE = "brake_commands"
RABBITMQ_EVENT_QUEUE = "events"
# RabbitMQ credentials
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
RABBITMQ_RECONNECT_DELAY = int(os.getenv("RABBITMQ_RECONNECT_DELAY", "5"))

# Flask app
app = Flask(__name__)

connection = None
channel = None


def init_db():
    """Initialize SQLite database for Central Director events."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            details TEXT NOT NULL
        )
    """
    )
    conn.commit()
    conn.close()


def init_rabbitmq():
    """Initialize RabbitMQ connection and channel."""
    try:
        global connection, channel
        logger.info("Connecting to RabbitMQ...")
        creds = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        params = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            credentials=creds,
            heartbeat=600,
            blocked_connection_timeout=300,
        )
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.queue_declare(queue=RABBITMQ_DM_QUEUE, durable=True)
        channel.queue_declare(queue=RABBITMQ_EVENT_QUEUE, durable=True)
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(queue=RABBITMQ_DM_QUEUE,
                              on_message_callback=dm_callback)
        channel.basic_consume(
            queue=RABBITMQ_EVENT_QUEUE, on_message_callback=event_callback
        )
        logger.info("RabbitMQ connection established")
        return True
    except Exception as e:
        logger.error(f"Failed to connect to RabbitMQ: {e}")
        return False


def dm_callback(ch, method, properties, body):
    """Callback for processing Distance Monitor messages."""
    logger.info(f"Received DM message: {body}")
    try:
        data = json.loads(body)
        process_message(data)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        logger.error(f"Error processing DM message: {e}", exc_info=True)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def event_callback(ch, method, properties, body):
    """Callback for processing generic event messages."""
    logger.info(f"Received event message: {body}")
    try:
        data = json.loads(body)
        process_message(data)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        logger.error(f"Error processing event message: {e}", exc_info=True)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def save_event(event_type, details):
    """Save an event to the database."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO events (timestamp, event_type, details) VALUES (?, ?, ?)",
        (datetime.utcnow().isoformat(), event_type, details),
    )
    conn.commit()
    conn.close()
    logger.info(f"Saved event: {event_type} - {details}")


def evaluate_rules(dm_data):
    """
    Evaluates decision-making rules based on distance and delta values.
    """
    distance = dm_data.get("distance")
    delta = dm_data.get("delta")
    # Validate inputs
    if distance is None or delta is None:
        return False, ""
    # Rule 1: distance < 20m and delta > 3 m/s
    if distance < 20 and delta > 3:
        return True, "DM: <20m & Δ>3m/s"
    # Rule 2: distance < 40m and delta > 5 m/s
    if distance < 40 and delta > 5:
        return True, "DM: <40m & Δ>5m/s"
    # Rule 3: deviation >20m between LT and DM (requires LT data to be implemented)
    # Currently not evaluated due to lack of live LT data
    return False, ""


def trigger_emergency_break(vehicle_id, reason):
    try:
        global connection, channel
        if channel is not None:
            # Construct and publish the brake message
            msg = {
                "command": "break",
                "vehicle_id": vehicle_id,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            }
            channel.basic_publish(
                exchange="",
                routing_key=RABBITMQ_EB_QUEUE,
                body=json.dumps(msg),
                properties=pika.BasicProperties(
                    delivery_mode=2
                ),  # make message persistent
            )
            logger.info(f"Published emergency brake for {vehicle_id}: {reason}")
            # Save event to database
            save_event("emergency_break", f"Published brake for {vehicle_id}: {reason}")
    except Exception as e:
        save_event("error", f"Failed to publish EB message: {e}")


def process_message(data):
    """
    Dispatch incoming messages:
      - Distance Monitor (contains 'delta')
      - Location Tracker (contains 'latitude' and 'longitude' or LT payload)
      - Log messages (contains 'log_message')
    """
    if "front_distance_m" in data and "front_velocity_mps" in data:
        logger.info(f"Processing Distance Monitor data: {data}")
        # Distance Monitor message
        vehicle = data.get("vehicle_id")
        distance = data.get("front_distance_m")
        delta = (data.get("front_velocity_mps", 0) or 0) - (
            data.get("rear_velocity_mps", 0) or 0
        )
        save_event("distance_monitor", f"{vehicle} distance={distance}, Δ={delta}")
        trigger, reason = evaluate_rules(
            {
                "vehicle_id": vehicle,
                "distance": distance,
                "delta": delta,
                "timestamp": data.get("timestamp"),
            }
        )
        if trigger:
            trigger_emergency_break(vehicle, reason)
    elif data.get("lat") is not None and data.get("lng") is not None:
        logger.info(f"Processing Location Tracker data: {data}")
        # Location Tracker message
        vehicle = data.get("vehicle_id")
        lat = data.get("lat")
        lng = data.get("lng")
        save_event("location_tracker", f"{vehicle} at ({lat}, {lng})")
    elif data.get("log_message"):
        # Generic log
        sender = data.get("log_sender", "unknown")
        msg = data.get("log_message")
        vehicle = data.get("vehicle_id", "unknown")
        save_event(sender, f"[{vehicle}] {msg}")
    else:
        logger.warning(f"Received unknown message format: {data}")
        save_event("unknown_message", json.dumps(data))


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM events")
        count = cursor.fetchone()[0]
        conn.close()
        return jsonify({"status": "ok", "events_logged": count}), 200
    except Exception as e:
        logger.error(f"Health check error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/logs", methods=["GET"])
def get_events():
    """Get recent events with pagination."""
    # Get pagination parameters
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 100, type=int)

    # Ensure page is at least 1
    page = max(1, page)

    # Calculate offset
    offset = (page - 1) * limit

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get total count for pagination metadata
    cursor.execute("SELECT COUNT(*) FROM events")
    total_count = cursor.fetchone()[0]

    # Get paginated results
    cursor.execute(
        "SELECT timestamp, event_type, details FROM events ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = cursor.fetchall()
    conn.close()

    events = [{"timestamp": ts, "type": et, "details": d}
              for ts, et, d in rows]

    # Calculate pagination metadata
    total_pages = (total_count + limit - 1) // limit  # Ceiling division
    has_next = page < total_pages
    has_prev = page > 1

    response = {
        "events": events,
        "pagination": {
            "page": page,
            "limit": limit,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next": has_next,
            "has_prev": has_prev,
        },
    }

    return jsonify(response), 200


def start_rabbit_consuming():
    logger.info("Starting RabbitMQ consumer thread")
    channel.start_consuming()

if __name__ == "__main__":
    init_db()
    # Start RabbitMQ consumer
    if not init_rabbitmq():
        logger.error("Failed to initialize RabbitMQ, exiting")
        sys.exit(1)

    # Start RabbitMQ consumer in a separate thread
    consumer_thread = threading.Thread(target=start_rabbit_consuming, daemon=True)
    consumer_thread.start()

    # Enable stdout/stderr flushing
    sys.stdout.flush()
    sys.stderr.flush()

    # Run Flask app
    logger.info("Starting Central Director Flask app")
    app.run(host="0.0.0.0", port=5000)
