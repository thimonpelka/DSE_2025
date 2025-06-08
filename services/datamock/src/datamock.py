import json
import os
import random
import threading
import time
import typing
from flask import Flask, Response, request, jsonify
from datetime import datetime
import requests
import logging
import sys
import pika

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)  # Log to stdout
    ],
)

vehicle_id = os.environ.get("VEHICLE_ID", f"VEHICLE_{random.randint(1000, 9999)}")

logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)

ENDPOINTS = {
    "http://location-sender/gps": ["gps"],
    "http://distance-monitor/sensor-data": ["ultrasonic", "radar", "camera", "lidar"],
}

SEND_INTERVAL = 0.1  # seconds

# Sensor range limits (in meters)
SENSOR_RANGES = {
    "radar": {"min": 0.5, "max": 200.0},
    "ultrasonic": {"min": 0.02, "max": 8.0},
    "camera": {"min": 1.0, "max": 150.0},
    "lidar": {"min": 0.1, "max": 300.0}
}

# Global variables for emergency braking
emergency_brake_state = {
    "is_braking": False,
    "brake_start_time": None,
    "brake_duration": 10,
    "brake_position": None
}
brake_lock = threading.Lock()

# Global simulator reference
global_simulator = None
simulator_lock = threading.Lock()

# RabbitMQ setup
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = "distance_data_"+ vehicle_id.replace("-","_")
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


def send_sensor_data_to_queue(data: dict) -> None:
    """Send sensor data (without GPS) to RabbitMQ queue"""
    global connection, channel

    # Extract only sensor data, excluding GPS
    sensor_message = {
        "timestamp": data.get("timestamp", ""),
        "vehicle_id": data["vehicle_id"],
        "ultrasonic": data.get("ultrasonic", {}),
        "radar": data.get("radar", {}),
        "camera": data.get("camera", {}),
        "lidar": data.get("lidar", {})
    }

    message = json.dumps(sensor_message)

    # Check connection status and attempt to reconnect if needed
    rabbitmq_connected = False
    if connection is None or not connection.is_open:
        logger.warning("RabbitMQ connection not available, attempting to reconnect")
        rabbitmq_connected = connect_to_rabbitmq()
    else:
        rabbitmq_connected = True

    # If we have a connection, publish the message
    if rabbitmq_connected:
        try:
            channel.basic_publish(
                exchange="",
                routing_key=RABBITMQ_QUEUE,
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Make message persistent
                    content_type="application/json",
                ),
            )
            logger.info("Sent sensor data to RabbitMQ: %s", message)
        except Exception as e:
            logger.error("Failed to publish sensor data to RabbitMQ: %s", str(e))
    else:
        logger.error("Failed to send sensor message - RabbitMQ connection unavailable")


# Initial connection attempt
connect_attempt = connect_to_rabbitmq()
if not connect_attempt:
    logger.warning(
        "Initial connection to RabbitMQ failed. Will retry on first request."
    )


def load_simulation_data(vehicle_id: str) -> dict:
    """Load simulation data from JSON file based on vehicle ID"""
    filename = f"{vehicle_id}-simulation-data.json"

    try:
        with open(filename, 'r') as file:
            data = json.load(file)
            logger.info(f"Loaded simulation data from {filename}")
            return data
    except FileNotFoundError:
        logger.error(f"Simulation data file {filename} not found")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON file {filename}: {e}")
        return None


def apply_sensor_deviation(distance_m: float, sensor_type: str) -> float:
    """Apply sensor-specific deviation to distance measurement"""
    if distance_m is None:
        return None

    # Define deviation ranges for each sensor type
    deviations = {
        "ultrasonic": random.uniform(-0.1, 0.1),  # ±10cm deviation
        "radar": random.uniform(-0.5, 0.5),  # ±50cm deviation
        "camera": random.uniform(-0.3, 0.3),  # ±30cm deviation
        "lidar": random.uniform(-0.05, 0.05)  # ±5cm deviation (high precision)
    }

    deviation = deviations.get(sensor_type, 0)
    adjusted_distance = distance_m + deviation

    # Ensure minimum distance constraints
    min_distance = SENSOR_RANGES[sensor_type]["min"]
    return max(min_distance, adjusted_distance)


def is_distance_in_sensor_range(distance_m: float, sensor_type: str) -> bool:
    """Check if distance is within sensor detection range"""
    if distance_m is None:
        return False

    sensor_range = SENSOR_RANGES[sensor_type]
    return sensor_range["min"] <= distance_m <= sensor_range["max"]


class VehicleSimulator:
    def __init__(self, vehicle_id: str):
        self.vehicle_id = vehicle_id
        self.simulation_data = load_simulation_data(vehicle_id)
        self.current_data_index = 0
        
        # Instead of using start_time, we'll calculate position based on absolute time
        # This makes the simulation appear to run continuously regardless of when it starts
        
        if not self.simulation_data:
            raise ValueError(f"Could not load simulation data for {vehicle_id}")

        self.data_points = self.simulation_data.get("data", [])
        self.simulation_info = self.simulation_data.get("simulation_info", {})
        
        # Calculate simulation duration (should be 2 minutes = 120,000 ms)
        if self.data_points:
            self.simulation_duration_ms = max(point["time_elapsed_ms"] for point in self.data_points)
        else:
            self.simulation_duration_ms = 120000  # Default to 2 minutes
            
        logger.info(f"Initialized simulator for {vehicle_id} with {len(self.data_points)} data points")
        logger.info(f"Simulation duration: {self.simulation_duration_ms}ms ({self.simulation_duration_ms/1000}s)")

    def get_current_simulation_time_ms(self) -> int:
        """Calculate where we should be in the simulation cycle based on current time"""
        # Use current Unix timestamp in milliseconds
        current_time_ms = int(time.time() * 1000)
        
        # Calculate position within the simulation cycle
        # This makes it appear as if the simulation has been running continuously
        simulation_position_ms = current_time_ms % self.simulation_duration_ms
        
        return simulation_position_ms

    def get_current_data_point(self) -> dict:
        """Get current simulation data point based on continuous simulation time"""
        if not self.data_points:
            return None

        # Get where we should be in the simulation cycle
        simulation_time_ms = self.get_current_simulation_time_ms()
        
        # Find the appropriate data point based on simulation time
        # Use binary search or linear search to find the right point
        for i, data_point in enumerate(self.data_points):
            if data_point["time_elapsed_ms"] >= simulation_time_ms:
                self.current_data_index = i
                return data_point
        
        # If we've passed all data points, return the last one
        # This shouldn't happen if simulation_duration_ms is calculated correctly
        self.current_data_index = len(self.data_points) - 1
        return self.data_points[-1]

    def generate_realistic_sensor_data(self, sim_data_point: dict) -> dict:
        """Generate sensor data based on simulation data with realistic deviations"""
        distances = sim_data_point.get("distances", {})
        front_distance_m = distances.get("front_distance_m")
        rear_distance_m = distances.get("rear_distance_m")

        sensor_data = {}

        # ULTRASONIC data (front_distance_cm, rear_distance_cm)
        if is_distance_in_sensor_range(front_distance_m, "ultrasonic"):
            adjusted_front = apply_sensor_deviation(front_distance_m, "ultrasonic")
            sensor_data["ultrasonic_front_distance_cm"] = int(adjusted_front * 100)
        else:
            sensor_data["ultrasonic_front_distance_cm"] = None  # No detection

        if is_distance_in_sensor_range(rear_distance_m, "ultrasonic"):
            adjusted_rear = apply_sensor_deviation(rear_distance_m, "ultrasonic")
            sensor_data["ultrasonic_rear_distance_cm"] = int(adjusted_rear * 100)
        else:
            sensor_data["ultrasonic_rear_distance_cm"] = None  # No detection

        # RADAR data (object_distance_m) - only front detection
        if is_distance_in_sensor_range(front_distance_m, "radar"):
            adjusted_front = apply_sensor_deviation(front_distance_m, "radar")
            sensor_data["radar_object_distance_m"] = round(adjusted_front, 2)
        else:
            sensor_data["radar_object_distance_m"] = None  # No detection

        # CAMERA data (front_estimate_m, rear_estimate_m)
        if is_distance_in_sensor_range(front_distance_m, "camera"):
            adjusted_front = apply_sensor_deviation(front_distance_m, "camera")
            sensor_data["camera_front_estimate_m"] = round(adjusted_front, 2)
        else:
            sensor_data["camera_front_estimate_m"] = None  # No detection

        if is_distance_in_sensor_range(rear_distance_m, "camera"):
            adjusted_rear = apply_sensor_deviation(rear_distance_m, "camera")
            sensor_data["camera_rear_estimate_m"] = round(adjusted_rear, 2)
        else:
            sensor_data["camera_rear_estimate_m"] = None  # No detection

        # LIDAR data (front_estimate_m, rear_estimate_m)
        if is_distance_in_sensor_range(front_distance_m, "lidar"):
            adjusted_front = apply_sensor_deviation(front_distance_m, "lidar")
            sensor_data["lidar_front_estimate_m"] = round(adjusted_front, 3)
        else:
            sensor_data["lidar_front_estimate_m"] = None  # No detection

        if is_distance_in_sensor_range(rear_distance_m, "lidar"):
            adjusted_rear = apply_sensor_deviation(rear_distance_m, "lidar")
            sensor_data["lidar_rear_estimate_m"] = round(adjusted_rear, 3)
        else:
            sensor_data["lidar_rear_estimate_m"] = None  # No detection

        return sensor_data

    def generate_data(self) -> dict[str, typing.Any]:
        timestamp = datetime.utcnow().isoformat() + "Z"

        # Get current simulation data point based on continuous time
        sim_data_point = self.get_current_data_point()
        if not sim_data_point:
            logger.error("No simulation data available")
            return {}

        # Extract position data from simulation
        position = sim_data_point.get("current_position", {})
        latitude = position.get("latitude", 0.0)
        longitude = position.get("longitude", 0.0)

        # Get realistic sensor data based on simulation distances
        sensor_data = self.generate_realistic_sensor_data(sim_data_point)

        # Log current simulation position for debugging
        current_sim_time = self.get_current_simulation_time_ms()
        logger.debug(f"Simulation time: {current_sim_time}ms ({current_sim_time/1000:.1f}s into cycle)")

        return {
            "timestamp": timestamp,
            "vehicle_id": self.vehicle_id,
            "gps": {
                "latitude": round(latitude, 6),
                "longitude": round(longitude, 6),
                "altitude_m": round(10.0 + random.uniform(-0.5, 0.5), 2),
                "accuracy_m": round(random.uniform(0.5, 5.0), 2),
            },
            "ultrasonic": {
                "front_distance_cm": sensor_data["ultrasonic_front_distance_cm"],
                "rear_distance_cm": sensor_data["ultrasonic_rear_distance_cm"],
            },
            "radar": {
                "object_distance_m": sensor_data["radar_object_distance_m"],
            },
            "camera": {
                "front_estimate_m": sensor_data["camera_front_estimate_m"],
                "rear_estimate_m": sensor_data["camera_rear_estimate_m"],
            },
            "lidar": {
                "front_estimate_m": sensor_data["lidar_front_estimate_m"],
                "rear_estimate_m": sensor_data["lidar_rear_estimate_m"],
            },
        }


def send_data_to_endpoints(full_data: dict[str, typing.Any]) -> None:
    headers = {"Content-Type": "application/json"}
    for endpoint, keys in ENDPOINTS.items():
        payload = {
            "timestamp": full_data["timestamp"],
            "vehicle_id": full_data["vehicle_id"],
        }
        for key in keys:
            if key in full_data:
                payload[key] = full_data[key]

        try:
            response = requests.post(
                endpoint, headers=headers, data=json.dumps(payload), timeout=5
            )
            logging.info(f"[{response.status_code}] Sent to {endpoint}")
        except requests.exceptions.RequestException as e:
            logging.warning(f"Error sending to {endpoint}: {e}")

    # Also send sensor data to RabbitMQ queue
    send_sensor_data_to_queue(full_data)


@app.route("/emergency-brake", methods=["POST"])
def emergency_brake() -> tuple[Response, int]:
    """Endpoint to trigger emergency braking"""
    logging.info(f"Received emergency brake request for vehicle {vehicle_id}")

    try:
        # Get payload
        data = request.get_json()
        if not data or "vehicle_id" not in data:
            return jsonify({"error": "Invalid data - vehicle_id required"}), 400

        payload_vehicle_id = data["vehicle_id"]

        # Check if this request is for this vehicle
        if payload_vehicle_id != vehicle_id:
            return jsonify({"error": f"Vehicle ID mismatch. This vehicle is {vehicle_id}"}), 400

        payload_timestamp = data.get("timestamp", datetime.utcnow().isoformat() + "Z")

        with brake_lock:
            if emergency_brake_state["is_braking"]:
                return jsonify({
                    "status": "already braking",
                    "vehicle_id": payload_vehicle_id,
                    "timestamp": payload_timestamp,
                    "remaining_brake_time_sec": emergency_brake_state["brake_duration"] -
                                                (time.time() - emergency_brake_state["brake_start_time"])
                }), 200

            # Get current position from the global simulator
            current_data_point = None
            with simulator_lock:
                if global_simulator:
                    current_data_point = global_simulator.get_current_data_point()

            if not current_data_point:
                return jsonify({"error": "Simulation not running"}), 500

            # Start emergency braking
            emergency_brake_state["is_braking"] = True
            emergency_brake_state["brake_start_time"] = time.time()
            brake_duration = emergency_brake_state["brake_duration"]
            emergency_brake_state["brake_position"] = current_data_point

        logging.info(f"Emergency brake activated for {brake_duration} seconds")

        return jsonify({
            "status": "emergency brake activated",
            "vehicle_id": payload_vehicle_id,
            "timestamp": payload_timestamp,
            "brake_duration_sec": brake_duration,
            "message": f"Vehicle will stop for {brake_duration} seconds and then resume"
        }), 200

    except Exception as e:
        logging.error(f"Error processing emergency brake request: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/brake-status", methods=["GET"])
def get_brake_status() -> Response:
    """Endpoint to get current brake status"""
    with brake_lock:
        if emergency_brake_state["is_braking"]:
            remaining_time = emergency_brake_state["brake_duration"] - (
                    time.time() - emergency_brake_state["brake_start_time"])
            return jsonify({
                "vehicle_id": vehicle_id,
                "is_braking": True,
                "remaining_brake_time_sec": max(0, remaining_time),
                "total_brake_duration_sec": emergency_brake_state["brake_duration"]
            })
        else:
            return jsonify({
                "vehicle_id": vehicle_id,
                "is_braking": False
            })


@app.route("/vehicle_id", methods=["GET"])
def get_vehicle_id() -> Response:
    """Endpoint to get the vehicle ID"""
    return jsonify({"vehicle_id": vehicle_id})


def start_simulation() -> None:
    global global_simulator

    try:
        with simulator_lock:
            global_simulator = VehicleSimulator(vehicle_id)

        logging.info(f"Starting JSON-based simulation for {vehicle_id}")

        while True:
            # Check if we're in emergency braking mode
            should_send_data = True
            with brake_lock:
                if emergency_brake_state["is_braking"]:
                    brake_elapsed = time.time() - emergency_brake_state["brake_start_time"]
                    if brake_elapsed < emergency_brake_state["brake_duration"]:
                        should_send_data = False
                        remaining_time = emergency_brake_state["brake_duration"] - brake_elapsed
                        logger.info(f"Emergency brake active - not sending data. {remaining_time:.1f}s remaining")
                    else:
                        # End emergency braking
                        emergency_brake_state["is_braking"] = False
                        emergency_brake_state["brake_start_time"] = None
                        emergency_brake_state["brake_duration"] = 10  # Keep at 10 seconds
                        emergency_brake_state["brake_position"] = None
                        logger.info(f"Emergency brake ended for {vehicle_id}, resuming data transmission")

            if should_send_data:
                full_data = global_simulator.generate_data()
                if full_data:  # Only send if we have valid data
                    send_data_to_endpoints(full_data)

            time.sleep(SEND_INTERVAL)

    except Exception as e:
        logging.error(f"Error in simulation: {e}", exc_info=True)


def start_flask() -> None:
    app.run(host="0.0.0.0", port=5000, debug=False)


def run() -> None:
    thread = threading.Thread(target=start_simulation, daemon=True)
    thread.start()


def run_flask() -> None:
    thread = threading.Thread(target=start_flask, daemon=True)
    thread.start()


if __name__ == "__main__":
    logging.info("Starting JSON-based vehicle sensor simulator...")
    run()
    run_flask()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopped.")