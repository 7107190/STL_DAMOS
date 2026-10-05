"""Replay protection for a one-way channel.

A challenge-response handshake needs a return path. A modality receiving an
anchor broadcast has none, so freshness cannot be receiver-nonce based. This
guard substitutes anchor-issued ordering:

  epoch_id  128-bit hex, redrawn whenever the anchor restarts or rotates keys
  seq_no    strictly increasing within an epoch, never reused

Receiver rules:
  - same epoch  : accept only seq_no > last accepted seq_no
  - new epoch   : accept only if its timestamp is strictly newer than the last
                  accepted timestamp, which blocks rolling back to a replayed
                  older epoch
  - every packet: must fall inside a bounded wall-clock acceptance window

What this does NOT give you, and should not be described as giving you:
  - protection against an attacker who suppresses packets (this is not a
    liveness or delivery guarantee)
  - protection against a compromised anchor issuing fresh valid packets
  - cross-recipient targeting beyond the committed recipient_ids list
State is per-process memory. Persistent state across restarts is out of scope.
"""
import threading


class ReplayGuard:
    def __init__(self, max_age_ms=2000, max_future_ms=500):
        if max_age_ms <= 0 or max_future_ms < 0:
            raise ValueError('INVALID_REPLAY_WINDOW')
        self.max_age_ms = int(max_age_ms)
        self.max_future_ms = int(max_future_ms)
        self._lock = threading.Lock()
        self._state = {}

    def check(self, anchor_id, epoch_id, seq_no, timestamp, now_ms):
        """Read-only admissibility test. Returns '' when admissible."""
        if type(seq_no) is not int or seq_no < 0:
            return 'INVALID_SEQ_NO'
        if type(timestamp) is not int:
            return 'INVALID_TIMESTAMP'
        if timestamp - now_ms > self.max_future_ms:
            return 'FUTURE_BROADCAST'
        if now_ms - timestamp > self.max_age_ms:
            return 'STALE_BROADCAST'
        with self._lock:
            previous = self._state.get(anchor_id)
        if previous is None:
            return ''
        last_epoch, last_seq, last_ts = previous
        if epoch_id == last_epoch:
            if seq_no <= last_seq:
                return 'REPLAY_OR_OUT_OF_ORDER'
            return ''
        if timestamp <= last_ts:
            return 'EPOCH_ROLLBACK'
        return ''

    def commit(self, anchor_id, epoch_id, seq_no, timestamp):
        """Called only after the packet has fully verified. An invalid packet
        must never advance the accepted watermark."""
        with self._lock:
            previous = self._state.get(anchor_id)
            if previous is not None:
                last_epoch, last_seq, last_ts = previous
                if epoch_id == last_epoch and seq_no <= last_seq:
                    return False
                if epoch_id != last_epoch and timestamp <= last_ts:
                    return False
            self._state[anchor_id] = (epoch_id, seq_no, timestamp)
            return True

    def watermark(self, anchor_id):
        with self._lock:
            return self._state.get(anchor_id)
