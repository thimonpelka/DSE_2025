import sys
import time
import os
import json
import logging
import threading
import pika
import requests
from flask import Flask, request, jsonify
import datetime

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger()

# Flask app
app = Flask(__name__)

# Environment variables
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS", "guest")
VEHICLE_ID = os.environ.get("VEHICLE_ID", "unknown")
INCOMING_QUEUE = "brake_commands"
OUTGOING_QUEUE = "brake_status"
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
                heartbeat=600,
                blocked_connection_timeout=300,
            )
        )
        channel = connection.channel()
        channel.queue_declare(queue=INCOMING_QUEUE, durable=True)
        channel.queue_declare(queue=OUTGOING_QUEUE, durable=True)
        logger.info("Successfully connected to RabbitMQ")
        return True
    except Exception as e:
        logger.error("Failed to connect to RabbitMQ: %s", str(e))
        connection = None
        channel = None
        return False


# Publish brake status
def publish_brake_success(vehicle_id):
    msg = {
        "vehicle_id": vehicle_id,
        "status": "brake_applied",
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }

    rabbitmq_connected = False
    if connection is None or not connection.is_open:
        logger.warning(
            "RabbitMQ connection not available, attempting to reconnect")
        rabbitmq_connected = connect_to_rabbitmq()
    else:
        rabbitmq_connected = True

    # If we have a connection, publish the message
    if rabbitmq_connected:
        channel.basic_publish(
            exchange="", routing_key=OUTGOING_QUEUE, body=json.dumps(msg)
        )
        logger.info(f"📤 Sent brake success for {vehicle_id} to queue.")
    else:
        logger.error(
            "Failed to send message - RabbitMQ connection unavailable")


# Process brake command from queue
def process_brake_command(vehicle_id):
    logger.warning(f"🚨 BRAKE COMMAND RECEIVED for {vehicle_id}")
    send_brake_signal_to_datamock(vehicle_id)
    publish_brake_success(vehicle_id)


# RabbitMQ consumer thread
def brake_command_listener():
    def callback(ch, method, properties, body):
        try:
            msg = json.loads(body)
            vehicle_id = msg.get("vehicle_id", VEHICLE_ID)
            if msg.get("command") == "brake":
                process_brake_command(vehicle_id)
        except Exception as e:
            logger.error(f"Error processing brake command: {e}", exc_info=True)

    channel.basic_consume(
        queue=INCOMING_QUEUE, on_message_callback=callback, auto_ack=True
    )
    logger.info("📡 Listening for brake commands on RabbitMQ...")
    channel.start_consuming()

def send_brake_signal_to_datamock(vehicle_id):
    """Send a brake signal to the datamock service."""
    endpoint = "http://datamock-service:5000/emergency-brake"
    payload = {
        "vehicle_id": vehicle_id,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            endpoint, headers=headers, data=json.dumps(payload), timeout=5
        )
        logger.info(f"[{response.status_code}] Sent brake signal to {endpoint}")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Error sending brake signal to {endpoint}: {e}")

# Flask route to receive processed sensor data
@app.route("/processed-data", methods=["POST"])
def receive_processed_data():
    try:
        data = request.get_json()
        vehicle_id = data.get("vehicle_id", VEHICLE_ID)
        timestamp = data.get("timestamp")
        front_distance = data.get("front_distance_m")
        front_velocity = data.get("front_velocity_mps")

        if vehicle_id is None or front_distance is None or front_velocity is None:
            return jsonify({"error": "Missing required fields"}), 400

        logger.info(
            f"Received from {vehicle_id} at {timestamp}: distance={front_distance}m, Δv={front_velocity}m/s"
        )

        danger = False
        reason = ""

        if front_distance < 20 and front_velocity < -3:
            danger = True
            reason = "CRITICAL: distance < 20m and closing rate > 3m/s"
        elif front_distance < 40 and front_velocity < -5:
            danger = True
            reason = "WARNING: distance < 40m and closing rate > 5m/s"

        if danger:
            logger.warning(
                f"🚨 EMERGENCY BRAKE TRIGGERED for {vehicle_id}! Reason: {reason}"
            )
            send_brake_signal_to_datamock(vehicle_id)
            publish_brake_success(vehicle_id)
            return jsonify(
                {"status": "emergency_brake_triggered", "reason": reason}
            ), 200
        else:
            return jsonify({"status": "safe"}), 200

    except Exception as e:
        logger.error(f"Error in emergency brake evaluation: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


# Start everything
if __name__ == "__main__":
    connect_attempt = connect_to_rabbitmq()
    if not connect_attempt:
        logger.warning(
            "Initial connection to RabbitMQ failed. Will retry on first request."
    )

    threading.Thread(target=brake_command_listener, daemon=True).start()
    logger.info("🚘 Emergency Brake Service running on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000)
