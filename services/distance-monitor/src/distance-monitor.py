import json
import os
import sys
import logging
from flask import Flask, request, jsonify
from datetime import datetime
import requests
import pika

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger()

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = os.getenv("RABBITMQ_QUEUE", "distance_data")
# RabbitMQ credentials
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
RABBITMQ_RECONNECT_DELAY = int(os.getenv("RABBITMQ_RECONNECT_DELAY", "5"))

app = Flask(__name__)

connection = None
channel = None

# Store last sensor readings and timestamps per vehicle for delta calculation
last_readings = {}


def connect_to_rabbitmq():
    """Connect to RabbitMQ and return connection and channel"""
    global connection, channel
    try:
        logger.info("Attempting to connect to RabbitMQ...")
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
        logger.error(f"Failed to connect to RabbitMQ: {e}")
        return False


def calculate_distance_meters(data):
    """
    Extract front and rear distances from sensor data in meters.
    Ultrasonic is in cm, convert to m.
    Radar and camera distances assumed in meters.
    Combines sensors by averaging all available front and rear distances.
    """

    front_distances = []
    rear_distances = []

    # Ultrasonic: convert cm to meters
    ultrasonic = data.get("ultrasonic", {})
    if "front_distance_cm" in ultrasonic:
        front_distances.append(ultrasonic["front_distance_cm"] / 100.0)
    if "rear_distance_cm" in ultrasonic:
        rear_distances.append(ultrasonic["rear_distance_cm"] / 100.0)

    # Radar: assumed front distance
    radar = data.get("radar", {})
    if "object_distance_m" in radar:
        front_distances.append(radar["object_distance_m"])

    # Camera: use front_estimate_m and rear_estimate_m if available
    camera = data.get("camera", {})
    if "front_estimate_m" in camera:
        front_distances.append(camera["front_estimate_m"])
    if "rear_estimate_m" in camera:
        rear_distances.append(camera["rear_estimate_m"])

    # Compute averages if we have any readings, else None
    front = sum(front_distances) / len(front_distances) if front_distances else None
    rear = sum(rear_distances) / len(rear_distances) if rear_distances else None

    return front, rear


def calculate_velocity(vehicle_id, front, rear, timestamp):
    """
    Calculate velocity (rate of change of distance) in meters/second.
    Use last readings stored in last_readings dict.
    """

    velocity = {"front_mps": None, "rear_mps": None}

    if vehicle_id in last_readings:
        prev = last_readings[vehicle_id]
        prev_time = prev.get("timestamp")
        prev_front = prev.get("front")
        prev_rear = prev.get("rear")

        if prev_time and prev_front is not None and front is not None:
            time_diff = (timestamp - prev_time).total_seconds()
            if time_diff > 0:
                velocity["front_mps"] = (front - prev_front) / time_diff

        if prev_time and prev_rear is not None and rear is not None:
            time_diff = (timestamp - prev_time).total_seconds()
            if time_diff > 0:
                velocity["rear_mps"] = (rear - prev_rear) / time_diff

    # Update last_readings
    last_readings[vehicle_id] = {
        "front": front,
        "rear": rear,
        "timestamp": timestamp,
    }

    return velocity


def send_processed_data(vehicle_id, front, rear, velocity, timestamp):
    # Construct the message payload
    msg = {
        "vehicle_id": vehicle_id,
        "front_distance_m": front,
        "rear_distance_m": rear,
        "front_velocity_mps": velocity["front_mps"],
        "rear_velocity_mps": velocity["rear_mps"],
        "timestamp": timestamp.isoformat(),
    }
    # First: send HTTP POST to Emergency Brake service
    url = "http://emergency-brake/processed-data"
    try:
        response = requests.post(url, json=msg, timeout=5)
        response.raise_for_status()
        logger.info(f"Sent processed data via HTTP for {vehicle_id}")
    except Exception as e:
        logger.error(f"Failed to send processed data via HTTP: {e}")
    # Second: publish the same payload to RabbitMQ
    try:
        global channel
        if channel is not None:
            channel.basic_publish(
                exchange="",
                routing_key=RABBITMQ_QUEUE,
                body=json.dumps(msg),
                properties=pika.BasicProperties(delivery_mode=2),  # persistent
            )
            logger.info(f"Published processed data for {vehicle_id}: {msg}")
    except Exception as e:
        logger.error(f"Failed to publish processed data: {e}")


@app.route("/sensor-data", methods=["POST"])
def receive_sensor_data():
    try:
        data = request.get_json()
        if not data or "vehicle_id" not in data:
            return jsonify({"error": "Missing 'vehicle_id' in data"}), 400

        vehicle_id = data["vehicle_id"]
        timestamp_str = data.get("timestamp")

        # Parse timestamp or use now if missing/invalid
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except Exception:
            timestamp = datetime.now(datetime.timezone.utc)

        front, rear = calculate_distance_meters(data.get("sensors", data))

        velocity = calculate_velocity(vehicle_id, front, rear, timestamp)

        # Instead of logging, send data to new endpoint
        send_processed_data(vehicle_id, front, rear, velocity, timestamp)

        return jsonify({"status": "processed"}), 200

    except Exception as e:
        logger.error(f"Error processing sensor data: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.logger.handlers = logger.handlers
    app.logger.setLevel(logger.level)
    logger.info("Starting Distance Monitor on 0.0.0.0:5000")
    if not connect_to_rabbitmq():
        logger.error("Failed to connect to RabbitMQ on startup, exiting")
        sys.exit(1)
    # Start Flask app
    app.run(host="0.0.0.0", port=5000)
