import unittest

from accessible_ensemble.core import (
    BarClock,
    EnsembleController,
    InteractionMode,
    MusicalIntent,
    NodDetector,
    TransportState,
    WeightedTempoSmoother,
    intent_to_mrt2,
)


class NodDetectorTests(unittest.TestCase):
    def test_down_and_return_fires_once(self):
        detector = NodDetector(minimum_magnitude=0.01)
        results = [
            detector.update(y)[0]
            for y in (0.40, 0.405, 0.42, 0.43, 0.425, 0.41)
        ]
        self.assertEqual(results.count(True), 1)


class TempoTests(unittest.TestCase):
    def test_stable_beats_converge(self):
        smoother = WeightedTempoSmoother(smoothing=0.0)
        for timestamp in (0.0, 0.5, 1.0, 1.5, 2.0):
            smoother.add_beat(timestamp)
        self.assertAlmostEqual(smoother.bpm, 120.0)

    def test_smoothing_reduces_jitter(self):
        raw = WeightedTempoSmoother(window=1, smoothing=0.0)
        smooth = WeightedTempoSmoother(window=1, smoothing=0.8)
        for timestamp in (0.0, 0.5, 1.0, 1.4):
            raw.add_beat(timestamp)
            smooth.add_beat(timestamp)
        self.assertLess(abs(smooth.bpm - 120), abs(raw.bpm - 120))


class IntentMappingTests(unittest.TestCase):
    def test_mapping_stays_in_performance_safe_range(self):
        low = intent_to_mrt2(MusicalIntent(
            energy=0, follow=0, pulse=0, adventure=0, style_commitment=0
        ))
        high = intent_to_mrt2(MusicalIntent(
            energy=1, follow=1, pulse=1, adventure=1, style_commitment=1
        ))
        self.assertGreaterEqual(low.temperature, 0.8)
        self.assertLessEqual(high.temperature, 1.55)
        self.assertGreaterEqual(low.top_k, 24)
        self.assertLessEqual(high.top_k, 140)
        self.assertAlmostEqual(low.cfg_notes, 0.8)
        self.assertAlmostEqual(high.cfg_notes, 4.2)
        self.assertAlmostEqual(high.cfg_drums, 4.5)
        self.assertAlmostEqual(high.cfg_musiccoca, 4.0)


class BarClockTests(unittest.TestCase):
    def test_beat_and_next_bar(self):
        clock = BarClock(4)
        clock.start(10.0, 120.0)
        self.assertEqual(clock.beat_number(10.1), 1)
        self.assertEqual(clock.beat_number(10.6), 2)
        self.assertAlmostEqual(clock.next_bar(10.1), 12.0)


class StateMachineTests(unittest.TestCase):
    def ready_controller(self):
        controller = EnsembleController(nods_to_start=4, smoothing=0.0)
        for timestamp in (0.0, 0.5, 1.0, 1.5):
            controller.record_nod(timestamp)
        self.assertEqual(controller.state.transport, TransportState.READY)
        return controller

    def test_start_is_armed_until_bar_boundary(self):
        controller = self.ready_controller()
        controller.request_start(1.6)
        self.assertEqual(controller.state.transport, TransportState.ARMED)
        self.assertAlmostEqual(controller.state.scheduled_at, 3.5)
        state, transition = controller.tick(3.5)
        self.assertEqual(transition, "start")
        self.assertEqual(state.transport, TransportState.ACTIVE)

    def test_hold_freezes_intent(self):
        controller = self.ready_controller()
        controller.request_start(1.6)
        controller.tick(3.5)
        controller.update_intent(energy=0.8)
        controller.request_hold()
        controller.update_intent(energy=0.1)
        self.assertEqual(controller.state.transport, TransportState.HOLD)
        self.assertAlmostEqual(controller.state.intent.energy, 0.8)

    def test_normal_stop_waits_for_next_bar(self):
        controller = self.ready_controller()
        controller.request_start(1.6)
        controller.tick(3.5)
        controller.request_stop(3.6)
        self.assertEqual(controller.state.transport, TransportState.STOP_QUEUED)
        controller.begin_stop_fade(5.5, 2.0)
        state, transition = controller.tick(7.5)
        self.assertEqual(transition, "stop")
        self.assertEqual(state.transport, TransportState.READY)

    def test_tracking_loss_enters_hold(self):
        controller = self.ready_controller()
        controller.request_start(1.6)
        controller.tick(3.5)
        controller.set_tracking(False)
        self.assertEqual(controller.state.transport, TransportState.HOLD)
        self.assertFalse(controller.state.tracking_ok)

    def test_mode_can_change(self):
        controller = self.ready_controller()
        controller.set_mode(InteractionMode.EXPERT)
        self.assertEqual(controller.state.mode, InteractionMode.EXPERT)

    def test_tempo_locks_after_twelfth_nod(self):
        controller = EnsembleController(
            nods_to_start=5,
            nods_to_lock=12,
            smoothing=0.0,
        )
        for index in range(12):
            controller.record_nod(index * 0.5)
        locked_bpm = controller.state.bpm
        controller.record_nod(6.25)
        controller.record_nod(7.25)
        self.assertTrue(controller.tempo_locked)
        self.assertEqual(controller.nod_count, 12)
        self.assertAlmostEqual(controller.state.bpm, locked_bpm)

    def test_tempo_lock_cannot_precede_start(self):
        with self.assertRaises(ValueError):
            EnsembleController(nods_to_start=5, nods_to_lock=4)


if __name__ == "__main__":
    unittest.main()
