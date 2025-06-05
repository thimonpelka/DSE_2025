import unittest
import json
import os
import tempfile
import sqlite3
import threading
import time
from unittest.mock import patch, MagicMock
import datetime

# Import the main application (assuming it's saved as gps_tracker.py)
# You may need to adjust this import based on your file structure
import sys

sys.path.append(".")

# Mock pika before importing the main module to avoid RabbitMQ dependency in tests
with patch("pika.BlockingConnection"), patch("pika.PlainCredentials"):
    from location_tracker import app, init_db, store_gps_data, DB_PATH


class GPSTrackerTestCase(unittest.TestCase):
    """Test suite for GPS Tracker application"""

    def setUp(self):
        """Set up test environment before each test"""
        # Create a temporary database for testing
        self.test_db_fd, self.test_db_path = tempfile.mkstemp()

        # Patch the DB_PATH to use our test database
        self.db_patcher = patch("location_tracker.DB_PATH", self.test_db_path)
        self.db_patcher.start()

        # Initialize the test database
        init_db()

        # Create test client
        app.config["TESTING"] = True
        self.client = app.test_client()

        # Sample GPS data for testing
        self.sample_gps_data = {
            "vehicle_id": "test_vehicle_001",
            "gps": {"latitude": 40.7128, "longitude": -74.0060},
            "timestamp": "2024-01-01T12:00:00Z",
        }

    def tearDown(self):
        """Clean up after each test"""
        # Stop the DB_PATH patch
        self.db_patcher.stop()

        # Close and remove the temporary database
        os.close(self.test_db_fd)
        os.unlink(self.test_db_path)

    def _insert_test_data(self, vehicle_id, lat_lon_pairs):
        """Helper method to insert test GPS data directly into database"""
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()

        for i, (lat, lon) in enumerate(lat_lon_pairs):
            timestamp = f"2024-01-01T{12 + i:02d}:00:00Z"
            cursor.execute(
                "INSERT INTO gps_data (vehicle_id, latitude, longitude, timestamp, created_at) VALUES (?, ?, ?, ?, ?)",
                (vehicle_id, lat, lon, timestamp, datetime.datetime.now(datetime.UTC).isoformat()),
            )

        conn.commit()
        conn.close()


class TestHealthCheck(GPSTrackerTestCase):
    """Test health check endpoint"""

    def test_health_check_success(self):
        """Test health check returns success when database is accessible"""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.data)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["database"], "connected")
        self.assertIn("record_count", data)
        self.assertEqual(data["record_count"], 0)  # Empty database initially


class TestGPSDataStorage(GPSTrackerTestCase):
    """Test GPS data storage functionality"""

    def test_store_valid_gps_data(self):
        """Test storing valid GPS data"""
        result = store_gps_data(self.sample_gps_data)
        self.assertTrue(result)

        # Verify data was stored in database
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM gps_data WHERE vehicle_id = ?", ("test_vehicle_001",)
        )
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[1], "test_vehicle_001")  # vehicle_id
        self.assertEqual(row[2], 40.7128)  # latitude
        self.assertEqual(row[3], -74.0060)  # longitude

    def test_store_invalid_gps_data(self):
        """Test storing invalid GPS data fails gracefully"""
        invalid_data = {
            "vehicle_id": "test_vehicle_002",
            "gps": {
                "latitude": None,  # Invalid: None latitude
                "longitude": -74.0060,
            },
        }

        result = store_gps_data(invalid_data)
        self.assertFalse(result)

        # Verify no data was stored
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM gps_data WHERE vehicle_id = ?", ("test_vehicle_002",)
        )
        count = cursor.fetchone()[0]
        conn.close()

        self.assertEqual(count, 0)


class TestVehicleLocationAPI(GPSTrackerTestCase):
    """Test vehicle location API endpoints"""

    def test_get_vehicle_location_single_position(self):
        """Test getting vehicle location with only one GPS record (no delta)"""
        # Insert single GPS record
        self._insert_test_data("vehicle_001", [(40.7128, -74.0060)])

        response = self.client.get("/api/vehicle/vehicle_001/location")
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.data)
        self.assertEqual(data["vehicle_id"], "vehicle_001")
        self.assertEqual(data["gps"]["latitude"], 40.7128)
        self.assertEqual(data["gps"]["longitude"], -74.0060)
        self.assertEqual(data["position_delta"]["latitude"], 0.0)
        self.assertEqual(data["position_delta"]["longitude"], 0.0)

    def test_get_vehicle_location_with_delta(self):
        """Test getting vehicle location with position delta calculation"""
        # Insert two GPS records to test delta calculation
        # Previous: (40.7128, -74.0060), Current: (40.7628, -74.0160)
        # Expected delta: (+0.05, -0.01)
        self._insert_test_data(
            "vehicle_002",
            [
                (40.7128, -74.0060),  # Previous position
                (40.7628, -74.0160),  # Current position
            ],
        )

        response = self.client.get("/api/vehicle/vehicle_002/location")
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.data)
        self.assertEqual(data["vehicle_id"], "vehicle_002")
        self.assertEqual(data["gps"]["latitude"], 40.7628)  # Current position
        self.assertEqual(data["gps"]["longitude"], -74.0160)
        self.assertEqual(data["position_delta"]
                         ["latitude"], 0.05)  # +0.05 change
        self.assertEqual(data["position_delta"]
                         ["longitude"], -0.01)  # -0.01 change

    def test_get_vehicle_location_not_found(self):
        """Test getting location for non-existent vehicle"""
        response = self.client.get("/api/vehicle/nonexistent_vehicle/location")
        self.assertEqual(response.status_code, 404)

        data = json.loads(response.data)
        self.assertIn("error", data)
        self.assertIn("nonexistent_vehicle", data["error"])


class TestLatestLocationsAPI(GPSTrackerTestCase):
    """Test latest locations API for all vehicles"""

    def test_get_latest_locations_multiple_vehicles(self):
        """Test getting latest locations for multiple vehicles with deltas"""
        # Insert data for multiple vehicles
        self._insert_test_data(
            "vehicle_A",
            [
                (40.7128, -74.0060),  # Previous
                (40.7228, -74.0160),  # Current (+0.01, -0.01)
            ],
        )

        self._insert_test_data(
            "vehicle_B",
            [
                (41.8781, -87.6298),  # Previous
                (41.8881, -87.6198),  # Current (+0.01, +0.01)
            ],
        )

        # Single position vehicle (no delta)
        self._insert_test_data("vehicle_C", [(34.0522, -118.2437)])

        response = self.client.get("/api/vehicles/latest-locations")
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.data)
        self.assertEqual(len(data), 3)  # Three vehicles

        # Find each vehicle in the response
        vehicles = {item["vehicle_id"]: item for item in data}

        # Check vehicle_A
        vehicle_a = vehicles["vehicle_A"]
        self.assertEqual(vehicle_a["gps"]["latitude"], 40.7228)
        self.assertEqual(vehicle_a["position_delta"]["latitude"], 0.01)
        self.assertEqual(vehicle_a["position_delta"]["longitude"], -0.01)

        # Check vehicle_B
        vehicle_b = vehicles["vehicle_B"]
        self.assertEqual(vehicle_b["gps"]["latitude"], 41.8881)
        self.assertEqual(vehicle_b["position_delta"]["latitude"], 0.01)
        self.assertEqual(vehicle_b["position_delta"]["longitude"], 0.01)

        # Check vehicle_C (no delta)
        vehicle_c = vehicles["vehicle_C"]
        self.assertEqual(vehicle_c["gps"]["latitude"], 34.0522)
        self.assertEqual(vehicle_c["position_delta"]["latitude"], 0.0)
        self.assertEqual(vehicle_c["position_delta"]["longitude"], 0.0)

    def test_get_latest_locations_empty_database(self):
        """Test getting latest locations when no vehicles exist"""
        response = self.client.get("/api/vehicles/latest-locations")
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.data)
        self.assertEqual(data, [])  # Empty list


class TestDeltaCalculationEdgeCases(GPSTrackerTestCase):
    """Test edge cases for delta calculations"""

    def test_large_coordinate_changes(self):
        """Test delta calculation with large coordinate changes"""
        # Test crossing hemispheres or large movements
        self._insert_test_data(
            "vehicle_large_move",
            [
                (-45.0, 170.0),  # Southern hemisphere, near dateline
                (45.0, -170.0),  # Northern hemisphere, other side of dateline
            ],
        )

        response = self.client.get("/api/vehicle/vehicle_large_move/location")
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.data)
        self.assertEqual(data["position_delta"]
                         ["latitude"], 90.0)  # +90 degrees
        self.assertEqual(data["position_delta"]
                         ["longitude"], -340.0)  # -340 degrees


if __name__ == "__main__":
    # Create a test suite
    test_suite = unittest.TestSuite()

    # Add all test classes
    test_classes = [
        TestHealthCheck,
        TestGPSDataStorage,
        TestVehicleLocationAPI,
        TestLatestLocationsAPI,
        TestDeltaCalculationEdgeCases,
    ]

    for test_class in test_classes:
        tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
        test_suite.addTests(tests)

    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)

    # Exit with appropriate code
    exit(0 if result.wasSuccessful() else 1)
