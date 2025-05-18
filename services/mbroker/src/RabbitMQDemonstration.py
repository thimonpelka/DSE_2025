# services/mbroker/src/rabbit_helper.py
import pika
import json
import os


class RabbitMQHelper:
    def __init__(self, host="rabbitmq", port=5672, username=None, password=None):
        """
        Initialize RabbitMQ connection

        :param host: RabbitMQ host (default: 'rabbitmq')
        :param port: RabbitMQ port (default: 5672)
        :param username: RabbitMQ username (optional)
        :param password: RabbitMQ password (optional)
        """
        # Attempt to read credentials from environment or use provided
        self.username = username or os.getenv(
            "RABBITMQ_USERNAME", "backend-user"
        )
        self.password = password or os.getenv(
            "RABBITMQ_PASSWORD", "secure-rabbitmq-password"
        )

        # Credentials
        credentials = pika.PlainCredentials(self.username, self.password)

        # Connection parameters
        self.connection_params = pika.ConnectionParameters(
            host=host, port=port, credentials=credentials
        )

    def publish_message(self, exchange="", routing_key="", message=None):
        """
        Publish a message to a specific exchange and routing key

        :param exchange: RabbitMQ exchange name
        :param routing_key: Routing key for message
        :param message: Message to send (dict or str)
        :return: True if message sent successfully
        """
        try:
            # Establish connection
            connection = pika.BlockingConnection(self.connection_params)
            channel = connection.channel()

            # Declare exchange if not default
            if exchange:
                channel.exchange_declare(
                    exchange=exchange, exchange_type="topic")

            # Convert message to JSON if it's a dict
            if isinstance(message, dict):
                message = json.dumps(message)

            # Publish message
            channel.basic_publish(
                exchange=exchange, routing_key=routing_key, body=message
            )

            # Close connection
            connection.close()
            return True
        except Exception as e:
            print(f"Error publishing message: {e}")
            return False

    def consume_messages(self, queue, callback):
        """
        Consume messages from a specific queue

        :param queue: Queue name to consume from
        :param callback: Function to process received messages
        """
        try:
            connection = pika.BlockingConnection(self.connection_params)
            channel = connection.channel()

            # Declare queue
            channel.queue_declare(queue=queue)

            # Set up consumer
            channel.basic_consume(
                queue=queue, on_message_callback=callback, auto_ack=True
            )

            print(f"Waiting for messages on queue {
                  queue}. To exit press CTRL+C")
            channel.start_consuming()
        except Exception as e:
            print(f"Error consuming messages: {e}")

    def create_queue(self, queue_name, durable=True, exclusive=False):
        """
        Create a queue

        :param queue_name: Name of the queue to create
        :param durable: Whether queue should survive broker restart
        :param exclusive: Whether queue is used by only one connection
        :return: True if queue created successfully
        """
        try:
            connection = pika.BlockingConnection(self.connection_params)
            channel = connection.channel()

            # Declare queue
            channel.queue_declare(
                queue=queue_name, durable=durable, exclusive=exclusive
            )

            connection.close()
            return True
        except Exception as e:
            print(f"Error creating queue: {e}")
            return False


# Example usage script
def example_usage():
    # Initialize RabbitMQ helper
    rabbit_mq = RabbitMQHelper()

    # Create some example queues
    rabbit_mq.create_queue("vehicle_locations")
    rabbit_mq.create_queue("vehicle_distances")

    # Publish a sample vehicle location message
    vehicle_location = {
        "vin": "ABC123",
        "latitude": 51.1657,
        "longitude": 10.4515,
        "timestamp": "2024-05-12T10:30:00Z",
    }

    rabbit_mq.publish_message(
        exchange="vehicle_events",
        routing_key="location.update",
        message=vehicle_location,
    )


if __name__ == "__main__":
    example_usage()
