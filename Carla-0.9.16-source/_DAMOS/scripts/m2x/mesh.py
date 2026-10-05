"""Independent per-actor processes; images travel directly over peer TCP sockets."""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import queue
import secrets
import signal
import time
import uuid

from .security import ImageVerifier, MAX_NODES, protect, real_backend
from .transport import Inbox, send_image


def pending_detector(local_image, shared_images):
    """Future detector entry point. No simulated detection or success result."""
    return {"status": "not_connected", "result": None, "elapsed_ms": None}


def neighbour_graph(samples, radius_m):
    """Ego-centred star: no observer-to-observer edges, even for radius=0."""
    graph = {node: [] for node in samples}
    for node, sample in samples.items():
        if node == "ego":
            continue
        distance2 = sum((a - b)**2 for a, b in zip(samples["ego"]["position"], sample["position"]))
        if radius_m == 0 or distance2 <= radius_m**2:
            graph["ego"].append(node)
            graph[node] = ["ego"]
    graph["ego"].sort()
    return graph


def _worker(node_id, commands, results, timeout_s, affinity):
    inbox = None
    try:
        if hasattr(os, "setsid"):
            os.setsid()
        # Bound each node's CPU allocation without changing the machine-wide runtime.
        if affinity and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, affinity)
        import cv2
        import numpy as np
        cv2.setNumThreads(1)
        backend = real_backend(timeout_s)
        from app.config import SNARK_FIELD_MODULUS
        secret = str(secrets.randbelow(SNARK_FIELD_MODULUS - 1) + 1)
        commitment = backend.compute_commitment(secret)
        inbox = Inbox()
        results.put({"event": "ready", "node_id": node_id,
                     "address": inbox.address, "commitment": commitment})
        settings = commands.get()
        if settings is None:
            return
        verifier = ImageVerifier(backend, node_id, settings["commitments"],
                                 settings["session_id"], max_age_ms=int(timeout_s * 1000))
        while True:
            task = commands.get()
            if task is None:
                break
            started = time.perf_counter_ns()
            deadline = time.monotonic() + timeout_s
            sample, graph = task["sample"], task["graph"]
            expected = graph[node_id]
            record = {
                "event": "round", "node_id": node_id, "round_id": task["round_id"],
                "frame_id": sample["frame_id"], "actor_id": sample["actor_id"],
                "capture_sim_ns": sample["capture_sim_ns"],
                "peer_ids": expected, "verified_peer_ids": [], "peer_images": {},
                "status": "error", "detection": {"status": "not_connected", "result": None,
                                                    "elapsed_ms": None},
                "metric2_pass": None, "path_planning": "excluded", "stages_ms": {},
                "transport_budget_pass": False,
            }
            images_to_save = {}
            try:
                if not expected:
                    record["status"] = "no_peers"
                else:
                    phase = time.perf_counter_ns()
                    bgra = np.frombuffer(sample["raw"], dtype=np.uint8).reshape(
                        sample["height"], sample["width"], 4)
                    own = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
                    ok, encoded = cv2.imencode(".png", own)
                    if not ok:
                        raise ValueError("PNG_ENCODING_FAILED")
                    image = encoded.tobytes()
                    record["stages_ms"]["encode"] = (time.perf_counter_ns() - phase) / 1e6
                    phase = time.perf_counter_ns()
                    packet, proof_timing = protect(backend, secret, commitment, image, {
                        "session_id": settings["session_id"], "round_id": task["round_id"],
                        "sender_id": node_id, "actor_id": sample["actor_id"],
                        "frame_id": sample["frame_id"], "capture_sim_ns": sample["capture_sim_ns"],
                        "width": sample["width"], "height": sample["height"],
                    }, expected)
                    record["stages_ms"]["protect"] = (time.perf_counter_ns() - phase) / 1e6
                    record["prove_ms"] = proof_timing["prove_total_ms"]
                    record["own_image_bytes"] = len(image)
                    record["own_image_sha256"] = hashlib.sha256(image).hexdigest()
                    phase = time.perf_counter_ns()
                    for peer in expected:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("SEND_DEADLINE")
                        send_image(settings["addresses"][peer], packet, image, min(2.0, remaining))
                    record["stages_ms"]["send"] = (time.perf_counter_ns() - phase) / 1e6
                    shared = {}
                    wait_ms, verify_ms, decode_ms = 0.0, 0.0, 0.0
                    while len(shared) < len(expected):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("RECEIVE_DEADLINE")
                        phase = time.perf_counter_ns()
                        try:
                            received, raw_image = inbox.items.get(timeout=remaining)
                        except queue.Empty as exc:
                            raise TimeoutError("MISSING_PEER") from exc
                        wait_ms += (time.perf_counter_ns() - phase) / 1e6
                        m = received.get("manifest", {})
                        # Packets left by a timed-out prior round are never fed to detection.
                        if m.get("round_id", -1) < task["round_id"]:
                            record["stale_packets_discarded"] = record.get("stale_packets_discarded", 0) + 1
                            continue
                        phase = time.perf_counter_ns()
                        sender = m.get("sender_id")
                        m, _ = verifier.verify(
                            received, raw_image, round_id=task["round_id"], frame_id=sample["frame_id"],
                            expected_senders=expected, expected_recipients=graph.get(sender, []))
                        verify_ms += (time.perf_counter_ns() - phase) / 1e6
                        if time.monotonic() > deadline:
                            raise TimeoutError("VERIFY_DEADLINE")
                        phase = time.perf_counter_ns()
                        decoded = cv2.imdecode(np.frombuffer(raw_image, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if decoded is None or decoded.shape != (m["height"], m["width"], 3):
                            raise ValueError("PNG_DECODING_FAILED")
                        shared[sender] = decoded
                        decode_ms += (time.perf_counter_ns() - phase) / 1e6
                        record["verified_peer_ids"].append(sender)
                        record["peer_images"][sender] = {
                            "sha256": m["image_sha256"], "bytes": len(raw_image),
                            "frame_id": m["frame_id"], "actor_id": m["actor_id"],
                        }
                        if node_id == "ego":
                            images_to_save[sender] = raw_image
                    record["stages_ms"].update(receive_wait=wait_ms, verify=verify_ms, decode=decode_ms)
                    record["detection"] = pending_detector(own, shared)
                    record["status"] = "sharing_complete_detection_pending"
                    images_to_save[node_id] = image
            except TimeoutError as exc:
                record.update(status="timeout", error=str(exc))
            except Exception as exc:
                record.update(status="error", error=type(exc).__name__ + ": " + str(exc))
            finally:
                elapsed_ms = (time.perf_counter_ns() - started) / 1e6
                record["elapsed_ms"] = elapsed_ms
                record["missing_peer_ids"] = sorted(set(expected) - set(record["verified_peer_ids"]))
                record["inbox_errors_total"] = inbox.errors
                record["transport_budget_pass"] = (
                    record["status"] == "sharing_complete_detection_pending" and elapsed_ms <= 18000)
                if elapsed_ms > timeout_s * 1000 and record["status"] == "sharing_complete_detection_pending":
                    record.update(status="timeout", transport_budget_pass=False, error="ROUND_DEADLINE")
                # Reporting and PNG writes are outside the measured interval.
                if node_id == "ego" and task.get("save_dir"):
                    try:
                        folder = Path(task["save_dir"])
                        folder.mkdir(parents=True, exist_ok=True)
                        for sender, content in images_to_save.items():
                            (folder / (sender + ".png")).write_bytes(content)
                    except OSError as exc:
                        record["artifact_error"] = str(exc)
                results.put(record)
    except Exception as exc:
        results.put({"event": "fatal", "node_id": node_id,
                     "error": type(exc).__name__ + ": " + str(exc)})
    finally:
        if inbox is not None:
            inbox.close()


class PeerMesh:
    """Trusted local enrollment; no image payload is relayed through the coordinator."""
    def __init__(self, node_ids, timeout_s=18.0, cpu_cores_per_node=2):
        if "ego" not in node_ids or len(set(node_ids)) != len(node_ids) or not 2 <= len(node_ids) <= MAX_NODES:
            raise ValueError("Mesh requires ego plus 1..31 distinct observers")
        self.node_ids, self.timeout_s = tuple(node_ids), timeout_s
        self.closed = False
        self.session_id = uuid.uuid4().hex
        self.processes, self.commands = {}, {}
        ctx = mp.get_context("spawn")
        self.results = ctx.Queue()
        cpus = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
        try:
            for index, node in enumerate(node_ids):
                command = ctx.Queue(maxsize=2)
                affinity = [cpus[(index * cpu_cores_per_node + j) % len(cpus)]
                            for j in range(min(cpu_cores_per_node, len(cpus)))] if cpus else []
                proc = ctx.Process(target=_worker, args=(node, command, self.results, timeout_s, affinity))
                proc.start()
                self.commands[node], self.processes[node] = command, proc
            ready = self._collect("ready", time.monotonic() + 90)
            self.addresses = {node: row["address"] for node, row in ready.items()}
            self.commitments = {node: row["commitment"] for node, row in ready.items()}
            for command in self.commands.values():
                command.put({"addresses": self.addresses, "commitments": self.commitments,
                             "session_id": self.session_id})
        except BaseException:
            self.close()
            raise

    def _collect(self, event, deadline, node_ids=None):
        expected = set(self.node_ids if node_ids is None else node_ids)
        rows = {}
        while len(rows) < len(expected):
            if any(not self.processes[node].is_alive() for node in expected):
                raise RuntimeError("PEER_PROCESS_EXITED")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("PEER_COLLECTION_TIMEOUT:" + ",".join(expected - set(rows)))
            try:
                row = self.results.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if row["event"] == "fatal":
                raise RuntimeError(row["node_id"] + ": " + row["error"])
            if row["event"] != event or row["node_id"] in rows or row["node_id"] not in expected:
                raise RuntimeError("UNEXPECTED_PEER_RESULT")
            rows[row["node_id"]] = row
        return rows

    def exchange(self, samples, round_id, radius_m=60.0, save_dir=None):
        if ("ego" not in samples or not set(samples) <= set(self.node_ids)
                or len({s["frame_id"] for s in samples.values()}) != 1):
            raise ValueError("ACTIVE_NODES_REQUIRE_THE_SAME_CARLA_FRAME")
        graph = neighbour_graph(samples, radius_m)
        active = ["ego", *graph["ego"]]
        for node in active:
            self.commands[node].put({"round_id": round_id, "sample": samples[node],
                                     "graph": graph, "save_dir": str(save_dir) if save_dir else None})
        # Allow a blocking prover to return its timed failure, but never mark it as a pass.
        rows = self._collect("round", time.monotonic() + 2 * self.timeout_s + 10, active)
        if any(row["round_id"] != round_id for row in rows.values()):
            raise RuntimeError("WRONG_PEER_RESULT_ROUND")
        return rows

    def close(self):
        if self.closed:
            return
        self.closed = True
        for command in self.commands.values():
            try:
                command.put_nowait(None)
            except queue.Full:
                pass
        for proc in self.processes.values():
            proc.join(timeout=1.0)
            if proc.is_alive():
                # Only our isolated group, including an in-flight snarkjs child.
                if hasattr(os, "getpgid") and os.getpgid(proc.pid) == proc.pid:
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
                proc.join(timeout=3.0)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=2.0)
        for command in self.commands.values():
            command.cancel_join_thread()
            command.close()
        self.results.cancel_join_thread()
        self.results.close()
