import json
import os
import math
import random
import threading
import time
import typing
from datetime import datetime
import requests
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)  # Log to stdout
    ]
)

logger = logging.getLogger(__name__)

# Configuration: Endpoint to data keys
ENDPOINTS = {
    "http://location-sender:5000/gps": ["gps"],
}
# ENDPOINTS = {
#     "http://localhost:5000/gps": ["gps"],
#     "http://localhost:5001/radar_lidar": ["radar", "lidar"],
#     "http://localhost:5002/ultrasonic": ["ultrasonic"],
#     "http://localhost:5003/camera": ["camera"],
#     "http://localhost:5004/imu": ["imu"],
#     "http://localhost:5005/wheel_encoder": ["wheel_encoder"],
#     "http://localhost:5006/temperature": ["temperature"],
#     "http://localhost:5007/battery": ["battery"],
#     "http://localhost:5008/can_bus": ["can_bus"],
#     "http://localhost:5009/full_state": [
#         "gps",
#         "radar",
#         "lidar",
#         "ultrasonic",
#         "camera",
#         "imu",
#         "wheel_encoder",
#         "temperature",
#         "battery",
#         "can_bus",
#     ],
# }
SEND_INTERVAL = 2  # seconds
EARTH_RADIUS_KM = 6371.0


class VehicleSimulator:
    def __init__(self, vehicle_id: str, lat: float = 40.0, lon: float = -74.0) -> None:
        self.vehicle_id = vehicle_id
        self.latitude = lat
        self.longitude = lon
        self.altitude = 10.0
        self.speed_kmh = 50.0
        self.last_update_time = time.time()

    def update_position(self) -> None:
        current_time = time.time()
        elapsed = current_time - self.last_update_time
        self.last_update_time = current_time

        # Evolve speed
        self.speed_kmh += random.uniform(-3, 3)
        self.speed_kmh = max(0, min(180, self.speed_kmh))

        # Move based on speed
        distance_km = (self.speed_kmh / 3600) * elapsed
        delta_lat = (distance_km / EARTH_RADIUS_KM) * (180 / math.pi)
        delta_lon = delta_lat / math.cos(math.radians(self.latitude))
        self.latitude += delta_lat
        self.longitude += delta_lon
        self.altitude += random.uniform(-0.5, 0.5)

    def generate_data(self) -> dict[str, typing.Any]:
        timestamp = datetime.utcnow().isoformat() + "Z"
        self.update_position()

        return {
            "timestamp": timestamp,
            "vehicle_id": self.vehicle_id,
            "gps": {
                "latitude": round(self.latitude, 6),
                "longitude": round(self.longitude, 6),
                "altitude_m": round(self.altitude, 2),
                "accuracy_m": round(random.uniform(0.5, 5.0), 2),
            },
            "radar": {
                "object_distance_m": round(random.uniform(1, 150), 2),
                "object_speed_kmh": round(random.uniform(-20, 120), 2),
                "signal_strength": round(random.uniform(0.1, 1.0), 2),
            },
            "lidar": {
                "point_cloud_density": random.randint(800, 2000),
                "avg_reflectivity": round(random.uniform(0.2, 1.0), 2),
                "object_count": random.randint(0, 20),
            },
            "ultrasonic": {
                "front_distance_cm": random.randint(20, 400),
                "rear_distance_cm": random.randint(20, 400),
                "side_distance_cm": [random.randint(20, 400) for _ in range(2)],
            },
            "camera": {
                "frame_quality": round(random.uniform(0.6, 1.0), 2),
                "lighting_level": round(random.uniform(0.3, 1.0), 2),
                "object_detection_count": random.randint(0, 10),
            },
            "imu": {
                "accel_x": round(random.uniform(-3.0, 3.0), 2),
                "accel_y": round(random.uniform(-3.0, 3.0), 2),
                "accel_z": round(random.uniform(-9.8, -7.0), 2),
                "gyro_x": round(random.uniform(-180, 180), 2),
                "gyro_y": round(random.uniform(-180, 180), 2),
                "gyro_z": round(random.uniform(-180, 180), 2),
            },
            "wheel_encoder": {
                "left_ticks": random.randint(1000, 10000),
                "right_ticks": random.randint(1000, 10000),
            },
            "temperature": {
                "sensor_board_temp_c": round(random.uniform(40, 85), 2),
                "ambient_temp_c": round(random.uniform(10, 40), 2),
            },
            "battery": {
                "voltage_v": round(random.uniform(11.5, 13.0), 2),
                "current_a": round(random.uniform(-20, 60), 2),
                "state_of_charge_percent": round(random.uniform(30, 100), 2),
            },
            "can_bus": {
                "packet_count": random.randint(1000, 5000),
                "error_rate": round(random.uniform(0.0, 0.05), 4),
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


def start_simulation() -> None:
    vehicle_id = os.environ.get("VEHICLE_ID", f"VEHICLE_{random.randint(1000,9999)}")
    simulator = VehicleSimulator(vehicle_id)
    while True:
        full_data = simulator.generate_data()
        send_data_to_endpoints(full_data)
        time.sleep(SEND_INTERVAL)


def run() -> None:
    thread = threading.Thread(target=start_simulation, daemon=True)
    thread.start()


if __name__ == "__main__":
    logging.info("Starting modular vehicle sensor simulator...")
    run()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopped.")
