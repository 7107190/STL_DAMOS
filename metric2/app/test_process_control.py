import unittest
from unittest.mock import patch

from process_control import terminate_record


class ProcessOwnership(unittest.TestCase):
    def test_missing_process_is_ignored(self):
        with patch('process_control.identity', side_effect=FileNotFoundError), patch('process_control.os.killpg') as group:
            terminate_record({'pid': 9999999, 'start_ticks': '100'})
            group.assert_not_called()

    def test_reused_pid_is_never_signalled(self):
        with patch('process_control.identity', return_value={'start_ticks': '200', 'state': 'R'}), patch('process_control.os.killpg') as group, patch('process_control.os.kill') as single:
            terminate_record({'pid': 9999999, 'start_ticks': '100'})
            group.assert_not_called()
            single.assert_not_called()

    def test_own_private_group_only(self):
        with patch('process_control.identity', side_effect=[{'start_ticks': '100', 'state': 'R'}, FileNotFoundError]), patch('process_control.os.getpgid', return_value=9999999), patch('process_control.os.killpg') as group, patch('process_control.os.kill') as single:
            terminate_record({'pid': 9999999, 'start_ticks': '100'})
            self.assertEqual(group.call_args.args[0], 9999999)
            single.assert_not_called()

    def test_nonprivate_group_only_signals_exact_child(self):
        with patch('process_control.identity', side_effect=[{'start_ticks': '100', 'state': 'R'}, FileNotFoundError]), patch('process_control.os.getpgid', return_value=8888888), patch('process_control.os.killpg') as group, patch('process_control.os.kill') as single:
            terminate_record({'pid': 9999999, 'start_ticks': '100'})
            group.assert_not_called()
            self.assertEqual(single.call_args.args[0], 9999999)

    def test_zombie_is_not_signalled(self):
        with patch('process_control.identity', return_value={'start_ticks': '100', 'state': 'Z'}), patch('process_control.os.killpg') as group:
            terminate_record({'pid': 9999999, 'start_ticks': '100'})
            group.assert_not_called()


if __name__ == '__main__':
    unittest.main()
