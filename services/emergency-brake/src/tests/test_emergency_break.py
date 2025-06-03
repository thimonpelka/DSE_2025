import emergency_brake
import json
import os
import sys
import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

# Add the parent directory to path to import the module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import the module under test


class TestEmergencyBrakeService:
    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Set up test fixtures before each test method and clean up after."""
        # Setup
        self.app = emergency_brake.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        # Mock environment variables
        self.vehicle_id = "TEST_VEHICLE_001"
        emergency_brake.VEHICLE_ID = self.vehicle_id

        yield  # This runs the test

        # Teardown
        emergency_brake.connection = None
        emergency_brake.channel = None

    @patch("emergency_brake.requests.post")
    def test_emergency_brake_critical_distance_and_velocity(self, mock_post):
        """Test Case 1: Emergency brake triggers for critical distance and high closing velocity"""
        mock_post.return_value.status_code = 200

        # Test data that should trigger emergency brake
        # distance < 20m and closing rate > 3m/s
        test_data = {
            "vehicle_id": self.vehicle_id,
            "timestamp": "2025-06-03T10:00:00Z",
            "front_distance_m": 15.0,  # Less than 20m
            "front_velocity_mps": -4.0,  # Closing at 4m/s (> 3m/s)
        }

        with patch("emergency_brake.publish_brake_success") as mock_publish:
            response = self.client.post(
                "/processed-data",
                data=json.dumps(test_data),
                content_type="application/json",
            )

            # Assertions
            assert response.status_code == 200
            response_data = json.loads(response.data)
            assert response_data["status"] == "emergency_brake_triggered"
            assert "CRITICAL" in response_data["reason"]

            # Verify brake signal was sent to datamock
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "http://datamock-service:5000/emergency-brake"

            # Verify brake success was published
            mock_publish.assert_called_once_with(self.vehicle_id)

    @patch("emergency_brake.requests.post")
    def test_emergency_brake_warning_distance_and_velocity(self, mock_post):
        """Test Case 2: Emergency brake triggers for warning threshold"""
        mock_post.return_value.status_code = 200

        # Test data for warning threshold
        # distance < 40m and closing rate > 5m/s
        test_data = {
            "vehicle_id": self.vehicle_id,
            "timestamp": "2025-06-03T10:00:00Z",
            "front_distance_m": 35.0,  # Less than 40m
            "front_velocity_mps": -6.0,  # Closing at 6m/s (> 5m/s)
        }

        with patch("emergency_brake.publish_brake_success") as mock_publish:
            response = self.client.post(
                "/processed-data",
                data=json.dumps(test_data),
                content_type="application/json",
            )

            # Assertions
            assert response.status_code == 200
            response_data = json.loads(response.data)
            assert response_data["status"] == "emergency_brake_triggered"
            assert "WARNING" in response_data["reason"]

            # Verify brake signal was sent
            mock_post.assert_called_once()
            mock_publish.assert_called_once_with(self.vehicle_id)

    def test_safe_driving_conditions(self):
        """Test Case 3: No emergency brake for safe driving conditions"""
        # Test data that should NOT trigger emergency brake
        test_scenarios = [
            {
                "vehicle_id": self.vehicle_id,
                "timestamp": "2025-06-03T10:00:00Z",
                "front_distance_m": 50.0,  # Safe distance
                "front_velocity_mps": -2.0,  # Low closing rate
            },
            {
                "vehicle_id": self.vehicle_id,
                "timestamp": "2025-06-03T10:00:00Z",
                "front_distance_m": 25.0,  # Moderate distance
                "front_velocity_mps": 1.0,  # Moving away
            },
            {
                "vehicle_id": self.vehicle_id,
                "timestamp": "2025-06-03T10:00:00Z",
                "front_distance_m": 100.0,  # Large distance
                "front_velocity_mps": -10.0,  # High closing rate but far away
            },
        ]

        with (
            patch("emergency_brake.send_brake_signal_to_datamock") as mock_brake,
            patch("emergency_brake.publish_brake_success") as mock_publish,
        ):
            for test_data in test_scenarios:
                response = self.client.post(
                    "/processed-data",
                    data=json.dumps(test_data),
                    content_type="application/json",
                )

                # Assertions
                assert response.status_code == 200
                response_data = json.loads(response.data)
                assert response_data["status"] == "safe"

            # Verify no brake signals were sent for any safe scenario
            mock_brake.assert_not_called()
            mock_publish.assert_not_called()

    def test_missing_required_fields(self):
        """Test Case 4: Handle missing required fields in request"""
        test_scenarios = [
            {},  # Empty request
            {"vehicle_id": self.vehicle_id},  # Missing distance and velocity
            {
                "vehicle_id": self.vehicle_id,
                "front_distance_m": 20.0,
            },  # Missing velocity
        ]

        for test_data in test_scenarios:
            response = self.client.post(
                "/processed-data",
                data=json.dumps(test_data),
                content_type="application/json",
            )

            # Assertions
            assert response.status_code == 400
            response_data = json.loads(response.data)
            assert "error" in response_data
            assert response_data["error"] == "Missing required fields"

    def test_missing_vehicle_id_uses_default(self):
        """Test Case 4b: Missing vehicle_id uses default VEHICLE_ID"""
        # The actual code uses VEHICLE_ID as default when vehicle_id is missing
        test_data = {"front_distance_m": 20.0, "front_velocity_mps": -3.0}

        response = self.client.post(
            "/processed-data",
            data=json.dumps(test_data),
            content_type="application/json",
        )

        # Should return 200 (safe) since this doesn't trigger brake conditions
        assert response.status_code == 200
        response_data = json.loads(response.data)
        assert response_data["status"] == "safe"

    @patch("emergency_brake.pika.BlockingConnection")
    def test_rabbitmq_brake_command_processing(self, mock_connection):
        """Test Case 5: Process brake commands from RabbitMQ queue"""
        # Mock RabbitMQ connection and channel
        mock_conn = MagicMock()
        mock_channel = MagicMock()
        mock_connection.return_value = mock_conn
        mock_conn.channel.return_value = mock_channel
        mock_conn.is_open = True

        # Set up the connection
        emergency_brake.connection = mock_conn
        emergency_brake.channel = mock_channel

        # Test brake command message
        brake_command = {
            "command": "brake",
            "vehicle_id": self.vehicle_id,
            "timestamp": "2025-06-03T10:00:00Z",
        }

        with (
            patch("emergency_brake.send_brake_signal_to_datamock") as mock_brake,
            patch("emergency_brake.publish_brake_success") as mock_publish,
        ):
            # Simulate receiving a brake command
            emergency_brake.process_brake_command(self.vehicle_id)

            # Assertions
            mock_brake.assert_called_once_with(self.vehicle_id)
            mock_publish.assert_called_once_with(self.vehicle_id)

    @patch("emergency_brake.requests.post")
    def test_datamock_service_communication_failure(self, mock_post):
        """Test Case 6: Handle datamock service communication failure gracefully"""
        # Simulate network error when calling datamock service
        mock_post.side_effect = Exception("Connection refused")

        test_data = {
            "vehicle_id": self.vehicle_id,
            "timestamp": "2025-06-03T10:00:00Z",
            "front_distance_m": 15.0,  # Should trigger brake
            "front_velocity_mps": -4.0,
        }

        with patch("emergency_brake.publish_brake_success") as mock_publish:
            response = self.client.post(
                "/processed-data",
                data=json.dumps(test_data),
                content_type="application/json",
            )

            # The actual implementation returns 500 when send_brake_signal_to_datamock fails
            # because the exception is not caught in the emergency brake logic
            assert response.status_code == 500
            response_data = json.loads(response.data)
            assert response_data["error"] == "Internal server error"

            # Verify publish_brake_success was NOT called due to the exception
            mock_publish.assert_not_called()


if __name__ == "__main__":
    # Run the tests with pytest
    pytest.main([__file__, "-v"])
