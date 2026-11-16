from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from m3_driving import maintain


class Stop:
    def __init__(self, polls):
        self.polls=polls
    def wait(self, delay):
        self.polls-=1
        return self.polls<0


class PostWindowDriving(unittest.TestCase):
    def test_pid_control_continues_after_acquisition(self):
        world=Mock()
        world.get_snapshot.side_effect=[SimpleNamespace(frame=i) for i in [100,100,101,102,103,104,105]]
        ego,agent=Mock(),Mock()
        agent.run_step.return_value="control"
        rows,errors=[],[]
        maintain(world,ego,agent,Stop(6),rows,errors)
        self.assertEqual([r["frame_id"] for r in rows],[100,102,104])
        self.assertEqual(ego.apply_control.call_count,3)
        self.assertEqual(errors,[])

    def test_native_autopilot_is_not_overwritten(self):
        world=Mock()
        world.get_snapshot.side_effect=[SimpleNamespace(frame=100),SimpleNamespace(frame=100)]
        ego,agent=Mock(),Mock()
        agent.run_step.return_value=None
        rows,errors=[],[]
        maintain(world,ego,agent,Stop(1),rows,errors)
        ego.apply_control.assert_not_called()
        self.assertFalse(rows[0]["control_applied"])

    def test_agent_error_is_reported_not_swallowed(self):
        world=Mock()
        world.get_snapshot.return_value=SimpleNamespace(frame=100)
        agent=Mock()
        agent.run_step.side_effect=RuntimeError("lost actor")
        rows,errors=[],[]
        maintain(world,Mock(),agent,Stop(1),rows,errors)
        self.assertEqual(errors,["RuntimeError: lost actor"])


if __name__=="__main__":
    unittest.main()
