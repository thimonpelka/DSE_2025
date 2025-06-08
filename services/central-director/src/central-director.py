import os
import threading
import json
import sqlite3
import logging
import sys
from datetime import datetime
from flask import Flask, request, jsonify
import pika
import requests
from requests.exceptions import RequestException

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
LT_SERVICE_URL = os.getenv("LT_SERVICE_URL", "http://location-tracker")
RABBITMQ_EB_QUEUE = "brake_commands"
RABBITMQ_EB_RESPONSE_QUEUE = "brake_status"
RABBITMQ_EVENT_QUEUE = "events"
# RabbitMQ credentials
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
RABBITMQ_RECONNECT_DELAY = int(os.getenv("RABBITMQ_RECONNECT_DELAY", "5"))

# Flask app
app = Flask(__name__)

connection = None
channel = None
vehicle_details = {}


def init_db():
    """Initialize SQLite database for Central Director events."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS events
        (
            id
            INTEGER
            PRIMARY
            KEY
            AUTOINCREMENT,
            timestamp
            TEXT
            NOT
            NULL,
            event_type
            TEXT
            NOT
            NULL,
            details
            TEXT
            NOT
            NULL
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
        channel.queue_declare(queue=RABBITMQ_EB_RESPONSE_QUEUE, durable=True)
        channel.queue_declare(queue=RABBITMQ_EVENT_QUEUE, durable=True)
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(queue=RABBITMQ_EB_RESPONSE_QUEUE,
                              on_message_callback=eb_callback)
        channel.basic_consume(
            queue=RABBITMQ_EVENT_QUEUE, on_message_callback=event_callback
        )
        logger.info("RabbitMQ connection established")
        return True
    except Exception as e:
        logger.error(f"Failed to connect to RabbitMQ: {e}")
        return False


def eb_callback(ch, method, properties, body):
    """Callback for processing Emergency Break messages."""
    logger.info(f"Received EB message: {body}")
    try:
        data = json.loads(body)
        vehicle_id = data.get("vehicle_id")
        if vehicle_id:
            if vehicle_id not in vehicle_details:
                vehicle_details[vehicle_id] = {'brake': False, 'front_distance': None, 'rear_distance': None,
                                               'front_distance_change': None, 'rear_distance_change': None}
            vehicle_details[vehicle_id]['brake'] = True
            timer = threading.Timer(10.0, lambda: vehicle_details[vehicle_id].update({'brake': False}))
            timer.start()
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


@app.route("/api/vehicles", methods=["GET"])
def get_vehicles_status():
    """Get brake status and distance for all vehicles."""
    try:
        vehicles = [{"vehicle_id": vid, "brake": details['brake'], "front_distance": details['front_distance'],
                     "rear_distance": details['rear_distance'],
                     "front_distance_change": details['front_distance_change'],
                     "rear_distance_change": details['rear_distance_change']}
                    for vid, details in vehicle_details.items()]
        return jsonify(vehicles), 200
    except Exception as e:
        logger.error(f"Error getting vehicle status: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


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


from math import radians, sin, cos, sqrt, atan2, degrees


def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two GPS coordinates using Haversine formula.
    Returns distance in meters.
    """
    R = 6371000  # Earth's radius in meters

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def calculate_bearing(lat1, lon1, lat2, lon2):
    """Calculate initial bearing between two points in degrees."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])

    dlon = lon2 - lon1
    y = sin(dlon) * cos(lat2)
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    bearing = degrees(atan2(y, x))

    return (bearing + 360) % 360


def get_lt_distance(vehicle_id):
    """
    Fetch and calculate distance data from Location Tracker service for a specific vehicle.
    Returns None if the data cannot be retrieved or calculated.
    """
    try:
        response = requests.get(f"{LT_SERVICE_URL}/api/vehicles/latest-locations", timeout=2.0)
        if response.status_code != 200:
            logger.warning(f"Failed to get LT locations. Status: {response.status_code}")
            return None

        locations = response.json()

        # Find our vehicle and its front vehicle
        current_vehicle = None
        front_vehicle = None
        min_front_distance = float('inf')

        # Find our vehicle first
        for loc in locations:
            if loc['vehicle_id'] == vehicle_id:
                current_vehicle = loc
                break

        if not current_vehicle:
            logger.warning(f"Vehicle {vehicle_id} not found in LT data")
            return None

        current_lat = current_vehicle['gps']['latitude']
        current_lon = current_vehicle['gps']['longitude']

        # Calculate current vehicle's bearing from its position delta
        current_bearing = None
        if 'position_delta' in current_vehicle:
            delta = current_vehicle['position_delta']
            if delta['latitude'] != 0 or delta['longitude'] != 0:
                current_bearing = calculate_bearing(
                    current_lat, current_lon,
                    current_lat + delta['latitude'],
                    current_lon + delta['longitude']
                )

        # Find the nearest vehicle in front
        for loc in locations:
            if loc['vehicle_id'] != vehicle_id:
                other_lat = loc['gps']['latitude']
                other_lon = loc['gps']['longitude']

                # Calculate distance
                distance = calculate_distance(
                    current_lat, current_lon,
                    other_lat, other_lon
                )

                # Calculate bearing to the other vehicle
                bearing_to_vehicle = calculate_bearing(
                    current_lat, current_lon,
                    other_lat, other_lon
                )

                # Check if vehicle is in front (within 45 degrees of current bearing)
                is_in_front = True
                if current_bearing is not None:
                    bearing_diff = abs(bearing_to_vehicle - current_bearing)
                    is_in_front = bearing_diff <= 45 or bearing_diff >= 315

                if distance < min_front_distance and is_in_front:
                    min_front_distance = distance
                    front_vehicle = loc

        if front_vehicle:
            return min_front_distance
        return None

    except RequestException as e:
        logger.error(f"Error fetching LT data: {e}")
        return None


def evaluate_rules(dm_data):
    """
    Evaluates decision-making rules based on distance and delta values.
    """
    vehicle_id = dm_data.get("vehicle_id")
    distance = dm_data.get("distance")
    delta = dm_data.get("delta")
    # Validate inputs
    if distance is None or delta is None:
        return False, ""
    # Rule 1: distance < 20m and delta < -3 m/s
    if distance < 20 and delta < -3:
        return True, "DM: <20m & Δ<-3m/s"
    # Rule 2: distance < 40m and delta < -5 m/s
    if distance < 40 and delta < -5:
        return True, "DM: <40m & Δ<-5m/s"
    # Rule 3: deviation >20 m between LT and DM (requires LT data to be implemented)
    lt_distance = get_lt_distance(vehicle_id)
    if lt_distance is not None:
        deviation = abs(lt_distance - distance)
        if deviation > 20:
            save_event("distance_deviation",
                       f"Vehicle {vehicle_id}: LT={lt_distance:.1f}m, DM={distance}m, Δ={deviation:.1f}m")
            return True, f"Deviation: LT vs DM >20m ({deviation:.1f}m)"

    return False, ""


def trigger_emergency_break(vehicle_id, reason):
    try:
        global connection, channel
        if connection is None or not connection.is_open:
            logger.warning(
                "RabbitMQ connection not available, attempting to reconnect")
            init_rabbitmq()
        if channel is not None:
            # Construct and publish the brake message
            msg = {
                "command": "brake",
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
        if vehicle:
            if vehicle not in vehicle_details:
                vehicle_details[vehicle] = {'brake': False, 'front_distance': None, 'rear_distance': None,
                                            'front_distance_change': None, 'rear_distance_change': None}
            vehicle_details[vehicle]['front_distance'] = distance
            vehicle_details[vehicle]['rear_distance'] = data.get("rear_distance_m")
            vehicle_details[vehicle]['front_distance_change'] = (data.get("front_velocity_mps", 0) or 0)
            vehicle_details[vehicle]['rear_distance_change'] = (data.get("front_velocity_mps", 0) or 0)
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


@app.route("/processed-data", methods=["POST"])
def receive_processed_data():
    """Receive processed data from Distance Monitor."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        logger.info(f"Received processed data: {data}")
        process_message(data)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Error processing data: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


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

    events = [{"timestamp": ts, "type": et, "details": d} for ts, et, d in rows]

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
