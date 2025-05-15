import json
import os
from flask import Flask, request, jsonify
import pika

app = Flask(__name__)

# RabbitMQ setup
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = "gps_data"

connection = pika.BlockingConnection(
    pika.ConnectionParameters(host=RABBITMQ_HOST))
channel = connection.channel()
channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)


@app.route("/gps", methods=["POST"])
def receive_gps() -> json:
    data = request.get_json()
    if not data or "vehicle_id" not in data or "gps" not in data:
        return jsonify({"error": "Invalid data"}), 400

    message = json.dumps(
        {
            "timestamp": data["timestamp"],
            "vehicle_id": data["vehicle_id"],
            "gps": data["gps"],
        }
    )

    channel.basic_publish(
        exchange="",
        routing_key=RABBITMQ_QUEUE,
        body=message,
        properties=pika.BasicProperties(delivery_mode=2),
    )

    return jsonify({"status": "sent"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
