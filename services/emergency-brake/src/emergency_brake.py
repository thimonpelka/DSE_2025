import datetime
import json
import logging
import os
import sys
import threading
import time

import pika
import requests
from flask import Flask, jsonify, request

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
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS", "guest")
RABBITMQ_EVENT_QUEUE = "events"
RABBITMQ_RECONNECT_DELAY = 5  # seconds between reconnection attempts
VEHICLE_ID = os.environ.get("VEHICLE_ID", "unknown")
INCOMING_QUEUE = "brake_commands"
OUTGOING_QUEUE = "brake_status"

# Thread-local storage for connections and channels
thread_local = threading.local()
connection_lock = threading.Lock()

def get_thread_local_connection():
    """Get or create a thread-local RabbitMQ connection and channel."""
    if not hasattr(thread_local, "connection") or thread_local.connection is None or thread_local.connection.is_closed:
        with connection_lock:
            if not hasattr(thread_local, "connection") or thread_local.connection is None or thread_local.connection.is_closed:
                try:
                    logger.info(f"Thread {threading.current_thread().name}: Attempting to connect to RabbitMQ...")
                    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
                    thread_local.connection = pika.BlockingConnection(
                        pika.ConnectionParameters(
                            host=RABBITMQ_HOST,
                            credentials=credentials,
                            heartbeat=600,
                            blocked_connection_timeout=300,
                        )
                    )
                    thread_local.channel = thread_local.connection.channel()
                    thread_local.channel.queue_declare(queue=INCOMING_QUEUE, durable=True)
                    thread_local.channel.queue_declare(queue=OUTGOING_QUEUE, durable=True)
                    thread_local.channel.queue_declare(queue=RABBITMQ_EVENT_QUEUE, durable=True)
                    logger.info(f"Thread {threading.current_thread().name}: Successfully connected to RabbitMQ")
                except Exception as e:
                    logger.error(f"Thread {threading.current_thread().name}: Failed to connect to RabbitMQ: {str(e)}")
                    thread_local.connection = None
                    thread_local.channel = None
                    raise
    return thread_local.connection, thread_local.channel

def publish_event(event_msg):
    """Publish an event to the RabbitMQ event queue."""
    msg = {
        "vehicle_id": VEHICLE_ID,
        "log_message": f"'{event_msg}' in {VEHICLE_ID}",
        "log_sender": "emergency_brake_service",
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }

    try:
        _, channel = get_thread_local_connection()
        channel.basic_publish(
            exchange="", routing_key=RABBITMQ_EVENT_QUEUE, body=json.dumps(msg)
        )
        logger.info(f"📤 Sent event '{event_msg}' for {VEHICLE_ID} to queue.")
    except Exception as e:
        logger.error(f"Failed to publish event: {e}", exc_info=True)

def publish_brake_success(vehicle_id):
    """Publish brake status to the outgoing queue."""
    msg = {
        "vehicle_id": vehicle_id,
        "status": "brake_applied",
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }

    try:
        _, channel = get_thread_local_connection()
        channel.basic_publish(
            exchange="", routing_key=OUTGOING_QUEUE, body=json.dumps(msg)
        )
        logger.info(f"📤 Sent brake success for {vehicle_id} to queue.")
    except Exception as e:
        logger.error(f"Failed to send brake status: {e}", exc_info=True)

def process_brake_command(vehicle_id):
    """Process a brake command."""
    publish_event("Processing valid brake command")
    logger.warning(f"🚨 BRAKE COMMAND RECEIVED for {vehicle_id}")
    send_brake_signal_to_datamock(vehicle_id)
    publish_brake_success(vehicle_id)

def brake_command_listener():
    """RabbitMQ consumer thread for brake commands."""
    def callback(ch, method, properties, body):
        try:
            publish_event("Received brake command")
            msg = json.loads(body)
            vehicle_id_msg = msg.get("vehicle_id", VEHICLE_ID)
            if msg.get("command") == "brake":
                if vehicle_id_msg != VEHICLE_ID:
                    logger.warning(
                        f"Received brake command for {vehicle_id_msg}, but this service is for {VEHICLE_ID}. Ignoring."
                    )
                    return
                process_brake_command(vehicle_id_msg)
        except Exception as e:
            logger.error(f"Error processing brake command: {e}", exc_info=True)

    try:
        _, channel = get_thread_local_connection()
        channel.basic_consume(
            queue=INCOMING_QUEUE, on_message_callback=callback, auto_ack=True
        )
        logger.info("📡 Listening for brake commands on RabbitMQ...")
        channel.start_consuming()
    except Exception as e:
        logger.error(f"Consumer thread failed: {e}", exc_info=True)
        time.sleep(RABBITMQ_RECONNECT_DELAY)
        brake_command_listener()  # Retry on failure

def send_brake_signal_to_datamock(vehicle_id):
    """Send a brake signal to the datamock service."""
    endpoint = "http://datamock-service/emergency-brake"
    payload = {
        "vehicle_id": vehicle_id,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            endpoint, headers=headers, data=json.dumps(payload), timeout=5
        )
        publish_event(f"Sent brake signal to {endpoint}")
        logger.info(f"[{response.status_code}] Sent brake signal to {endpoint}")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Error sending brake signal to {endpoint}: {e}")

@app.route("/processed-data", methods=["POST"])
def receive_processed_data():
    """Flask route to receive processed sensor data."""
    try:
        data = request.get_json()
        vehicle_id = data.get("vehicle_id", VEHICLE_ID)
        timestamp = data.get("timestamp")
        front_distance = data.get("front_distance_m")
        front_velocity = data.get("front_velocity_mps")

        if vehicle_id is None or front_distance is None or front_velocity is None:
            logger.warning(
                f"Received incomplete data from {vehicle_id}: {data}"
            )
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
            return (
                jsonify({"status": "emergency_brake_triggered", "reason": reason}),
                200,
            )
        else:
            return jsonify({"status": "safe"}), 200

    except Exception as e:
        logger.error(f"Error in emergency brake evaluation: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    # Start consumer thread
    threading.Thread(target=brake_command_listener, daemon=True).start()
    logger.info("🚘 Emergency Brake Service running on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000)