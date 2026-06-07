import unittest
from unittest.mock import patch

from accessible_ensemble.core import MusicalIntent
from accessible_ensemble.performer import Mrt2AuAdapter


class FakeOscClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.messages = []

    def send_message(self, address, value):
        self.messages.append((address, value))


class Mrt2AuAdapterTests(unittest.TestCase):
    def test_maps_intent_to_reaper_fx_parameters(self):
        with patch(
            "accessible_ensemble.performer.udp_client.SimpleUDPClient",
            FakeOscClient,
        ):
            adapter = Mrt2AuAdapter(port=8000, track=2, fx=1)
            adapter.update(MusicalIntent())

        addresses = {address for address, _value in adapter.client.messages}
        self.assertIn("/track/2/fx/1/fxparam/1/value", addresses)
        self.assertIn("/track/2/fx/1/fxparam/2/value", addresses)
        self.assertIn("/track/2/fx/1/fxparam/4/value", addresses)
        self.assertIn("/track/2/fx/1/fxparam/5/value", addresses)
        self.assertIn("/track/2/fx/1/fxparam/49/value", addresses)
        for _address, value in adapter.client.messages:
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_start_and_stop_control_reaper_fx_bypass(self):
        with patch(
            "accessible_ensemble.performer.udp_client.SimpleUDPClient",
            FakeOscClient,
        ):
            adapter = Mrt2AuAdapter(port=8000, track=3, fx=2)
            adapter.start()
            adapter.emergency_stop()

        self.assertEqual(
            adapter.client.messages,
            [
                ("/track/3/fx/2/bypass", 0),
                ("/track/3/fx/2/bypass", 1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
