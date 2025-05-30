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
import math

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)  # Log to stdout
    ]
)

logger = logging.getLogger(__name__)

# Configuration: Endpoint to data keys

# Works across all environments
# ENDPOINTS = {
#     "http://location-sender.backend.svc.cluster.local/gps": ["gps"],
# }

# Works only within the same namespace
SAMPLE_ROUTE = [
    (48.202349, 16.369632),
    (48.203518, 16.364254),
    (48.207107, 16.359645),
    (48.213656, 16.361653),
    (48.217606, 16.370971),
    (48.214020, 16.374217),
    (48.211647, 16.378848),
    (48.211160, 16.385109),
    (48.205869, 16.382407),
    (48.204504, 16.383228),
    (48.201791, 16.380067),
    (48.203289, 16.376624),
    (48.201387, 16.373687),
]

ENDPOINTS = {
    "http://location-sender/gps": ["gps"],
    "http://distance-monitor/sensor-data": ["ultrasonic", "radar", "camera"],
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

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # Earth radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
class VehicleSimulator:
    def __init__(self, vehicle_id: str, route: list[tuple[float, float]], speed_kmh=50.0):
        self.vehicle_id = vehicle_id
        self.route = route
        self.speed_kmh = speed_kmh
        self.current_index = 0
        self.latitude, self.longitude = route[0]
        self.altitude = 10.0
        self.last_update_time = time.time()

        

    def move_along_route(self):
        current_time = time.time()
        elapsed = current_time - self.last_update_time
        self.last_update_time = current_time

        if self.current_index >= len(self.route) - 1:
            self.current_index = 0  # Loop route

        lat1, lon1 = self.latitude, self.longitude
        lat2, lon2 = self.route[self.current_index + 1]

        distance_to_next_km = haversine(lat1, lon1, lat2, lon2)
        distance_travelled_km = (self.speed_kmh / 3600) * elapsed

        if distance_travelled_km >= distance_to_next_km:
            # Reach next waypoint
            self.latitude, self.longitude = lat2, lon2
            self.current_index += 1
        else:
            # Move fractionally along path
            fraction = distance_travelled_km / distance_to_next_km
            self.latitude = lat1 + (lat2 - lat1) * fraction
            self.longitude = lon1 + (lon2 - lon1) * fraction

        # Simulate altitude noise
        self.altitude += random.uniform(-0.5, 0.5)

    def generate_data(self) -> dict[str, typing.Any]:
        timestamp = datetime.utcnow().isoformat() + "Z"
        self.move_along_route()

        true_front_distance_m = round(random.uniform(1, 10), 2)
        true_rear_distance_m = round(random.uniform(1, 5), 2)

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
                "object_distance_m": round(true_front_distance_m + random.uniform(-0.5, 0.5), 2),
                "object_speed_kmh": round(random.uniform(-10, 50), 2),
                "signal_strength": round(random.uniform(0.3, 1.0), 2),
            },
            "lidar": {
                "point_cloud_density": random.randint(1000, 2000),  # High density = good resolution
                "avg_reflectivity": round(random.uniform(0.4, 1.0), 2),
                "object_count": random.randint(1, 3),
                "front_estimate_m": round(true_front_distance_m + random.uniform(-0.2, 0.2), 2),
                "rear_estimate_m": round(true_rear_distance_m + random.uniform(-0.2, 0.2), 2),
            },
            "ultrasonic": {
                "front_distance_cm": int((true_front_distance_m + random.uniform(-0.1, 0.1)) * 100),
                "rear_distance_cm": int((true_rear_distance_m + random.uniform(-0.1, 0.1)) * 100),
                "side_distance_cm": [random.randint(30, 200), random.randint(30, 200)],
            },
            "camera": {
                "frame_quality": round(random.uniform(0.6, 1.0), 2),
                "lighting_level": round(random.uniform(0.3, 1.0), 2),
                "object_detection_count": random.randint(0, 10),
                "front_estimate_m": round(true_front_distance_m + random.uniform(-0.3, 0.3), 2),
                "rear_estimate_m": round(true_rear_distance_m + random.uniform(-0.3, 0.3), 2),
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
    simulator = VehicleSimulator(vehicle_id, SAMPLE_ROUTE)
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
