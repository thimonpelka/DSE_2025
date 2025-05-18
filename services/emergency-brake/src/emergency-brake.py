import sys
import logging
from flask import Flask, request, jsonify
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger()

app = Flask(__name__)

@app.route("/processed-data", methods=["POST"])
def receive_processed_data():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing data"}), 400

        vehicle_id = data.get("vehicle_id")
        timestamp = data.get("timestamp")
        front_distance = data.get("front_distance_m")
        front_velocity = data.get("front_velocity_mps")

        if vehicle_id is None or front_distance is None or front_velocity is None:
            return jsonify({"error": "Missing required fields"}), 400

        logger.info(f"Received from {vehicle_id} at {timestamp}: distance={front_distance}m, Δv={front_velocity}m/s")

        # Decision logic
        danger = False
        reason = ""

        if front_distance < 20 and front_velocity < -3:
            danger = True
            reason = "CRITICAL: distance < 20m and closing rate > 3m/s"
        elif front_distance < 40 and front_velocity < -5:
            danger = True
            reason = "WARNING: distance < 40m and closing rate > 5m/s"

        if danger:
            logger.warning(f"🚨 EMERGENCY BRAKE TRIGGERED for {vehicle_id}! Reason: {reason}")
            # Optional: Integrate actual brake actuation logic here.
            return jsonify({"status": "emergency_brake_triggered", "reason": reason}), 200
        else:
            return jsonify({"status": "safe"}), 200

    except Exception as e:
        logger.error(f"Error in emergency brake evaluation: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    logger.info("Starting Emergency Brake Service on 0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5000)
