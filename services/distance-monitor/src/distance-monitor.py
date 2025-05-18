import json
import sys
import logging
from flask import Flask, request, jsonify
from datetime import datetime
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger()

app = Flask(__name__)

# Store last sensor readings and timestamps per vehicle for delta calculation
last_readings = {}

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
    url = "http://emergency-brake/processed-data" 
    
    payload = {
        "vehicle_id": vehicle_id,
        "front_distance_m": front,
        "rear_distance_m": rear,
        "front_velocity_mps": velocity["front_mps"],
        "rear_velocity_mps": velocity["rear_mps"],
        "timestamp": timestamp.isoformat(),
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send processed data to {url}: {e}")

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
    app.run(host="0.0.0.0", port=5000)
