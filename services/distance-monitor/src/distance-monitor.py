import json
import os
import sys
import logging
from flask import Flask, request, jsonify
from datetime import datetime
import requests
import pika
import threading
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger()

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = os.getenv("RABBITMQ_QUEUE", "sensor_data")
# RabbitMQ credentials
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
RABBITMQ_RECONNECT_DELAY = int(os.getenv("RABBITMQ_RECONNECT_DELAY", "5"))

# Deployment mode detection
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "vehicle")  # "vehicle" or "backend"
VEHICLE_ID_FILTER = os.getenv("VEHICLE_ID_FILTER", None)  # Only used in backend mode

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
                heartbeat=600,
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
    Radar, camera, and lidar distances assumed in meters.
    Combines sensors by averaging all available front and rear distances.
    Handles None values by filtering them out before averaging.
    """

    front_distances = []
    rear_distances = []

    # Ultrasonic: convert cm to meters
    ultrasonic = data.get("ultrasonic", {})
    if "front_distance_cm" in ultrasonic and ultrasonic["front_distance_cm"] is not None:
        front_distances.append(ultrasonic["front_distance_cm"] / 100.0)
    if "rear_distance_cm" in ultrasonic and ultrasonic["rear_distance_cm"] is not None:
        rear_distances.append(ultrasonic["rear_distance_cm"] / 100.0)

    # Radar: assumed front distance
    radar = data.get("radar", {})
    if "object_distance_m" in radar and radar["object_distance_m"] is not None:
        front_distances.append(radar["object_distance_m"])

    # Camera: use front_estimate_m and rear_estimate_m if available
    camera = data.get("camera", {})
    if "front_estimate_m" in camera and camera["front_estimate_m"] is not None:
        front_distances.append(camera["front_estimate_m"])
    if "rear_estimate_m" in camera and camera["rear_estimate_m"] is not None:
        rear_distances.append(camera["rear_estimate_m"])

    # LiDAR: use front_estimate_m and rear_estimate_m if available
    lidar = data.get("lidar", {})
    if "front_estimate_m" in lidar and lidar["front_estimate_m"] is not None:
        front_distances.append(lidar["front_estimate_m"])
    if "rear_estimate_m" in lidar and lidar["rear_estimate_m"] is not None:
        rear_distances.append(lidar["rear_estimate_m"])

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
    """Send processed data to appropriate endpoint based on deployment mode"""
    # Construct the message payload
    msg = {
        "vehicle_id": vehicle_id,
        "front_distance_m": front,
        "rear_distance_m": rear,
        "front_velocity_mps": velocity["front_mps"],
        "rear_velocity_mps": velocity["rear_mps"],
        "timestamp": timestamp.isoformat(),
    }

    # Determine target URL based on deployment mode
    if DEPLOYMENT_MODE == "backend":
        url = "http://central-director/processed-data"
        mode_desc = "backend -> central-director"
    else:
        url = "http://emergency-brake/processed-data"
        mode_desc = "vehicle -> emergency-brake"

    # Send HTTP POST
    try:
        response = requests.post(url, json=msg, timeout=5)
        response.raise_for_status()
        logger.info(f"Sent processed data via HTTP for {vehicle_id} ({mode_desc})")
    except Exception as e:
        logger.error(f"Failed to send processed data via HTTP ({mode_desc}): {e}")

    # Only publish to RabbitMQ if in vehicle mode
    if DEPLOYMENT_MODE == "vehicle":
        try:
            global channel
            if channel is not None:
                channel.basic_publish(
                    exchange="",
                    routing_key=RABBITMQ_QUEUE,
                    body=json.dumps(msg),
                    properties=pika.BasicProperties(delivery_mode=2),  # persistent
                )
                logger.info(f"Published processed data to RabbitMQ for {vehicle_id}")
        except Exception as e:
            logger.error(f"Failed to publish processed data to RabbitMQ: {e}")


def process_sensor_data(data):
    """Common function to process sensor data regardless of input source"""
    if not data or "vehicle_id" not in data:
        logger.warning("Missing 'vehicle_id' in sensor data")
        return False

    vehicle_id = data["vehicle_id"]

    # Filter: only process data for the specified vehicle (backend mode only)
    if DEPLOYMENT_MODE == "backend" and VEHICLE_ID_FILTER and vehicle_id != VEHICLE_ID_FILTER:
        logger.debug(f"Ignoring data for vehicle {vehicle_id} (filtering for {VEHICLE_ID_FILTER})")
        return True

    timestamp_str = data.get("timestamp")

    # Parse timestamp or use now if missing/invalid
    try:
        if timestamp_str:
            # Handle both formats
            if timestamp_str.endswith('Z'):
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                timestamp = datetime.fromisoformat(timestamp_str)
        else:
            timestamp = datetime.now(datetime.timezone.utc)
    except Exception:
        timestamp = datetime.now(datetime.timezone.utc)

    # Extract sensor data - handle both nested and flat structures
    sensor_data = data.get("sensors", data)
    front, rear = calculate_distance_meters(sensor_data)

    velocity = calculate_velocity(vehicle_id, front, rear, timestamp)

    # Send processed data to appropriate endpoint
    send_processed_data(vehicle_id, front, rear, velocity, timestamp)

    return True


@app.route("/sensor-data", methods=["POST"])
def receive_sensor_data():
    """HTTP endpoint for receiving sensor data (vehicle mode only)"""
    if DEPLOYMENT_MODE != "vehicle":
        return jsonify({"error": "HTTP endpoint not available in backend mode"}), 404

    try:
        data = request.get_json()
        if process_sensor_data(data):
            return jsonify({"status": "processed"}), 200
        else:
            return jsonify({"error": "Invalid data"}), 400

    except Exception as e:
        logger.error(f"Error processing sensor data via HTTP: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


def process_sensor_message(ch, method, properties, body):
    """Process incoming sensor data from RabbitMQ queue (backend mode only)"""
    try:
        logger.info("Received sensor data from RabbitMQ queue...")
        data = json.loads(body.decode('utf-8'))
        logger.info(f"Received sensor data: {data}")
        process_sensor_data(data)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON message: {e}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        logger.error(f"Error processing sensor message from RabbitMQ: {e}", exc_info=True)
        ch.basic_ack(delivery_tag=method.delivery_tag)


def start_rabbitmq_consumer():
    """Start consuming messages from RabbitMQ queue (backend mode only)"""
    global connection, channel

    if DEPLOYMENT_MODE != "backend":
        return

    while True:
        try:
            if not connect_to_rabbitmq():
                logger.error(f"Failed to connect to RabbitMQ, retrying in {RABBITMQ_RECONNECT_DELAY} seconds...")
                time.sleep(RABBITMQ_RECONNECT_DELAY)
                continue

            # Set up consumer
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=RABBITMQ_QUEUE,
                on_message_callback=process_sensor_message
            )

            logger.info(
                f"Started consuming sensor data from queue '{RABBITMQ_QUEUE}' for vehicle '{VEHICLE_ID_FILTER}'")
            channel.start_consuming()

        except pika.exceptions.AMQPConnectionError:
            logger.error("AMQP connection error, attempting to reconnect...")
            time.sleep(RABBITMQ_RECONNECT_DELAY)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, stopping consumer...")
            if channel:
                channel.stop_consuming()
            break
        except Exception as e:
            logger.error(f"Unexpected error in RabbitMQ consumer: {e}", exc_info=True)
            time.sleep(RABBITMQ_RECONNECT_DELAY)


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "mode": DEPLOYMENT_MODE,
        "vehicle_filter": VEHICLE_ID_FILTER if DEPLOYMENT_MODE == "backend" else None
    }), 200


if __name__ == "__main__":
    app.logger.handlers = logger.handlers
    app.logger.setLevel(logger.level)

    logger.info(f"Starting Distance Monitor in {DEPLOYMENT_MODE} mode")

    if DEPLOYMENT_MODE == "vehicle":
        logger.info("Vehicle mode: HTTP endpoint active, RabbitMQ publishing enabled")
        logger.info("Target: emergency-brake/processed-data")

        # Connect to RabbitMQ for publishing (vehicle mode)
        if not connect_to_rabbitmq():
            logger.error("Failed to connect to RabbitMQ on startup, exiting")
            sys.exit(1)

        # Start Flask app
        app.run(host="0.0.0.0", port=5000)

    elif DEPLOYMENT_MODE == "backend":
        logger.info(f"Backend mode: RabbitMQ consumer active for vehicle '{VEHICLE_ID_FILTER}'")
        logger.info("Target: central-director/processed-data")

        # Start RabbitMQ consumer in a separate thread
        consumer_thread = threading.Thread(target=start_rabbitmq_consumer, daemon=True)
        consumer_thread.start()

        # Start Flask app for health checks
        app.run(host="0.0.0.0", port=5000)

    else:
        logger.error(f"Invalid DEPLOYMENT_MODE: {DEPLOYMENT_MODE}. Must be 'vehicle' or 'backend'")
        sys.exit(1)