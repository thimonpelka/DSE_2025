import math
import json

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

# Simulation parameters
TIME_STEP = 0.1  # 100ms
SIMULATION_DURATION = 120  # 2 minutes


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two GPS coordinates in meters"""
    R = 6371000  # Earth radius in meters

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def get_vehicle_config(vehicle_id):
    """Get vehicle configuration based on ID"""
    if "vehicle-1" in vehicle_id.lower() or vehicle_id.endswith("-1"):
        return {
            "speed_kmh": 25.0,
            "start_waypoint": 2,  # Start 2 waypoints ahead
        }
    else:
        return {
            "speed_kmh": 70.0,
            "start_waypoint": 0,  # Start at beginning
        }


def calculate_route_distances():
    """Calculate cumulative distances along the route"""
    distances = [0.0]
    for i in range(1, len(SAMPLE_ROUTE)):
        prev_lat, prev_lon = SAMPLE_ROUTE[i - 1]
        curr_lat, curr_lon = SAMPLE_ROUTE[i]
        segment_dist = haversine_distance(prev_lat, prev_lon, curr_lat, curr_lon)
        distances.append(distances[-1] + segment_dist)
    return distances


def interpolate_position(waypoint_idx, progress, route):
    """Interpolate GPS position between waypoints"""
    if waypoint_idx >= len(route) - 1:
        return route[-1]

    lat1, lon1 = route[waypoint_idx]
    lat2, lon2 = route[waypoint_idx + 1]

    lat = lat1 + (lat2 - lat1) * progress
    lon = lon1 + (lon2 - lon1) * progress

    return (lat, lon)


def get_vehicle_position(vehicle_id, elapsed_time_sec):
    """Calculate vehicle position based on time elapsed"""
    config = get_vehicle_config(vehicle_id)
    speed_ms = config["speed_kmh"] / 3.6  # Convert km/h to m/s
    start_waypoint = config["start_waypoint"]

    route_distances = calculate_route_distances()

    # Calculate distance traveled from start waypoint
    start_distance = route_distances[start_waypoint]
    distance_traveled = speed_ms * elapsed_time_sec
    current_distance = start_distance + distance_traveled

    # Handle route looping
    total_route_distance = route_distances[-1]
    if current_distance > total_route_distance:
        current_distance = current_distance % total_route_distance

    # Find current waypoint segment
    for i in range(len(route_distances) - 1):
        if current_distance <= route_distances[i + 1]:
            segment_progress = ((current_distance - route_distances[i]) /
                                (route_distances[i + 1] - route_distances[i]))
            return interpolate_position(i, segment_progress, SAMPLE_ROUTE), i

    # Fallback to last position
    return SAMPLE_ROUTE[-1], len(SAMPLE_ROUTE) - 1


def calculate_inter_vehicle_distance(pos1, pos2):
    """Calculate distance between two vehicles"""
    lat1, lon1 = pos1
    lat2, lon2 = pos2
    return haversine_distance(lat1, lon1, lat2, lon2)


def determine_vehicle_relationship(elapsed_time_sec):
    """Determine which vehicle is in front based on simulation time"""
    config1 = get_vehicle_config("vehicle-1")
    config2 = get_vehicle_config("vehicle-2")

    route_distances = calculate_route_distances()

    # Calculate total distance traveled by each vehicle
    v1_distance = (config1["speed_kmh"] / 3.6) * elapsed_time_sec + route_distances[config1["start_waypoint"]]
    v2_distance = (config2["speed_kmh"] / 3.6) * elapsed_time_sec + route_distances[config2["start_waypoint"]]

    return v1_distance > v2_distance  # True if vehicle-1 is ahead


def calculate_front_rear_distances(vehicle_id, other_vehicle_distance, is_other_in_front):
    """Calculate front and rear distances for a vehicle"""
    if is_other_in_front:
        # Other vehicle is in front
        front_distance = other_vehicle_distance
        rear_distance = None  # No vehicle behind
    else:
        # Other vehicle is behind
        front_distance = None  # No vehicle in front
        rear_distance = other_vehicle_distance

    return front_distance, rear_distance


def generate_vehicle_data(vehicle_id, elapsed_time_sec, other_vehicle_distance, is_other_in_front):
    """Generate minimal data for a single vehicle"""
    config = get_vehicle_config(vehicle_id)
    position, waypoint = get_vehicle_position(vehicle_id, elapsed_time_sec)

    # Calculate front and rear distances
    front_distance, rear_distance = calculate_front_rear_distances(
        vehicle_id, other_vehicle_distance, is_other_in_front
    )

    # Convert elapsed time to milliseconds
    time_elapsed_ms = int(elapsed_time_sec * 1000)

    data = {
        "time_elapsed_ms": time_elapsed_ms,
        "vehicle_id": vehicle_id,
        "current_position": {
            "latitude": round(position[0], 6),
            "longitude": round(position[1], 6),
            "waypoint_index": waypoint
        },
        "speed_kmh": config["speed_kmh"],
        "distances": {
            "front_distance_m": round(front_distance, 2) if front_distance else None,
            "rear_distance_m": round(rear_distance, 2) if rear_distance else None
        }
    }

    return data


def generate_simulation_data():
    """Generate complete simulation data for both vehicles"""
    print("Generating simulation data...")
    print(f"Time step: {TIME_STEP}s ({int(TIME_STEP * 1000)}ms)")
    print(f"Duration: {SIMULATION_DURATION}s")

    vehicle1_data = []
    vehicle2_data = []

    # Generate data points
    current_time = 0.0
    step_count = 0

    while current_time <= SIMULATION_DURATION:
        # Get vehicle positions
        v1_pos, _ = get_vehicle_position("vehicle-1", current_time)
        v2_pos, _ = get_vehicle_position("vehicle-2", current_time)

        # Calculate inter-vehicle distance
        inter_vehicle_distance = calculate_inter_vehicle_distance(v1_pos, v2_pos)

        # Determine vehicle relationship
        v1_ahead = determine_vehicle_relationship(current_time)

        # Generate data for both vehicles
        v1_data = generate_vehicle_data(
            "vehicle-1", current_time, inter_vehicle_distance, not v1_ahead
        )

        v2_data = generate_vehicle_data(
            "vehicle-2", current_time, inter_vehicle_distance, v1_ahead
        )

        vehicle1_data.append(v1_data)
        vehicle2_data.append(v2_data)

        # Progress tracking
        if step_count % 100 == 0:  # Every 10 seconds
            print(
                f"Time: {current_time:.1f}s ({int(current_time * 1000)}ms), Distance: {inter_vehicle_distance:.2f}m, V1 ahead: {v1_ahead}")

        current_time += TIME_STEP
        step_count += 1

    return vehicle1_data, vehicle2_data


def save_simulation_files():
    """Generate and save simulation data to files"""
    vehicle1_data, vehicle2_data = generate_simulation_data()

    # Save vehicle-1 data
    with open("services/datamock/src/vehicle-1-simulation-data.json", "w") as f:
        json.dump({
            "vehicle_id": "vehicle-1",
            "simulation_info": {
                "time_step_ms": int(TIME_STEP * 1000),
                "duration_sec": SIMULATION_DURATION,
                "total_data_points": len(vehicle1_data),
                "vehicle_config": get_vehicle_config("vehicle-1")
            },
            "data": vehicle1_data
        }, f, indent=2)

    # Save vehicle-2 data
    with open("services/datamock/src/vehicle-2-simulation-data.json", "w") as f:
        json.dump({
            "vehicle_id": "vehicle-2",
            "simulation_info": {
                "time_step_ms": int(TIME_STEP * 1000),
                "duration_sec": SIMULATION_DURATION,
                "total_data_points": len(vehicle2_data),
                "vehicle_config": get_vehicle_config("vehicle-2")
            },
            "data": vehicle2_data
        }, f, indent=2)

    print(f"\nSimulation complete!")
    print(f"Generated {len(vehicle1_data)} data points for each vehicle")
    print(f"Time range: 0ms to {int(SIMULATION_DURATION * 1000)}ms")
    print(f"Files saved:")
    print(f"  - vehicle-1-simulation-data.json")
    print(f"  - vehicle-2-simulation-data.json")


if __name__ == "__main__":
    print("=== VEHICLE SIMULATION DATA GENERATOR ===\n")
    save_simulation_files()