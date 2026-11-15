"""Independent GPU inference peers, direct TCP Groth16 BEV sharing and ego fusion."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import secrets
import signal
import time
import uuid

import numpy as np

from bev_fusion import fuse_bevs
from bev_model_runtime import BEVPredictor, render_bev
from bev_occupancy import render_presence,render_presence_comparison
from bev_protocol import BEVVerifier, protect_bev
from bev_sensor_rig import prepare_raw_task
from m2x.security import real_backend, MAX_NODES
from m2x.transport import Inbox, send_image


def _inputs(task):
    if "arrays" in task:
        return task["arrays"], {"input_source": "saved_sensor_replay"}
    return prepare_raw_task(task)


def _worker(node, commands, results, timeout_s, affinity):
    inbox = None
    try:
        if hasattr(os, "setsid"):
            os.setsid()
        if affinity and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, affinity)
        import cv2
        cv2.setNumThreads(1)
        setup_start = time.monotonic()
        predictor = None
        backend = real_backend(timeout_s)
        from app.config import SNARK_FIELD_MODULUS
        secret = str(secrets.randbelow(SNARK_FIELD_MODULUS - 1) + 1)
        commitment = backend.compute_commitment(secret)
        inbox = Inbox()
        results.put({"event": "ready", "node_id": node, "commitment": commitment,
                     "address": inbox.address, "process_key_setup_s": time.monotonic()-setup_start,
                     "checkpoint_sha256": None})
        settings = commands.get()
        if settings is None:
            return
        verifier = BEVVerifier(backend, node, settings["commitments"], settings["session_id"],
                               settings["actor_ids"], max_age_ms=int(timeout_s*1000))
        service_cache = {}
        while True:
            task = commands.get()
            if task is None:
                break
            if task["action"] == "warmup":
                phase = time.monotonic()
                if predictor is None:
                    predictor = BEVPredictor()
                arrays, _ = _inputs(task["input"])
                _, timing = predictor.infer(arrays)
                results.put({"event": "warmup", "node_id": node, "timing": timing,
                             "load_and_warmup_s": time.monotonic()-phase,
                             "checkpoint_sha256": predictor.checkpoint_hash})
                continue
            if task["action"] == "release":
                import gc
                del predictor
                predictor = None
                gc.collect()
                import torch
                torch.cuda.empty_cache()
                results.put({"event": "release", "node_id": node})
                continue
            if task["action"] == "service_round":
                from bev_resilience import run_service_round
                results.put(run_service_round(node, task, settings, predictor, backend, secret,
                    commitment, verifier, inbox, service_cache, _inputs))
                continue
            started = task["started_ns"]
            deadline_ns = started + int(timeout_s*1e9)
            graph, sample = task["graph"], task["input"]
            expected = graph[node]
            row = {"event": "round", "node_id": node, "round_id": task["round_id"],
                   "frame_id": sample["window"][-1], "actor_id": sample["actor_id"],
                   "peer_ids": expected, "verified_peer_ids": [], "stages_ms": {},
                   "status": "error", "metric2_pass": None,
                   "object_recovery": "not_evaluated", "pipeline_budget_pass": False}
            own, fused, shared, arrays = None, None, {}, None
            try:
                if not expected:
                    raise ValueError("NO_NEIGHBOURS_NOT_AN_EVALUATION")
                if predictor is None:
                    raise RuntimeError("MODEL_NOT_PREPARED")
                phase = time.perf_counter_ns()
                arrays, input_record = _inputs(sample)
                row["input"] = input_record
                row["stages_ms"]["preprocess"] = (time.perf_counter_ns()-phase)/1e6
                own, infer_timing = predictor.infer(arrays)
                row["inference"] = infer_timing
                row["stages_ms"]["inference"] = infer_timing["input_to_output_ms"]
                phase = time.perf_counter_ns()
                packet, body, proof_timing = protect_bev(backend, secret, commitment, own, {
                    "session_id": settings["session_id"], "round_id": task["round_id"],
                    "sender_id": node, "actor_id": sample["actor_id"],
                    "frame_id": sample["window"][-1],
                    "capture_sim_ns": round(float(arrays["timestamps"][-1])*1e9),
                    "checkpoint_sha256": predictor.checkpoint_hash, "checkpoint_step": predictor.step,
                }, expected)
                row["stages_ms"]["protect"] = (time.perf_counter_ns()-phase)/1e6
                row["proof"] = proof_timing
                row["body_bytes"] = len(body)
                row["own_bev_sha256"] = packet["manifest"]["sha256"]
                row["received_bevs"] = {}
                phase = time.perf_counter_ns()
                for peer in expected:
                    remaining = (deadline_ns-time.perf_counter_ns())/1e9
                    if remaining <= 0:
                        raise TimeoutError("SEND_DEADLINE")
                    send_image(settings["addresses"][peer], packet, body, min(2., remaining))
                row["stages_ms"]["send"] = (time.perf_counter_ns()-phase)/1e6
                wait_ms, verify_ms = 0., 0.
                while len(shared) < len(expected):
                    remaining = (deadline_ns-time.perf_counter_ns())/1e9
                    if remaining <= 0:
                        raise TimeoutError("RECEIVE_DEADLINE")
                    phase = time.perf_counter_ns()
                    received, content = inbox.items.get(timeout=remaining)
                    wait_ms += (time.perf_counter_ns()-phase)/1e6
                    manifest = received.get("manifest", {})
                    if manifest.get("round_id", -1) < task["round_id"]:
                        continue
                    sender = manifest.get("sender_id")
                    phase = time.perf_counter_ns()
                    decoded, verify_timing = verifier.verify(
                        received, content, round_id=task["round_id"], frame_id=sample["window"][-1],
                        expected_senders=expected, expected_recipients=graph.get(sender, []))
                    if (manifest["capture_sim_ns"] != packet["manifest"]["capture_sim_ns"]
                            or manifest["checkpoint_sha256"] != predictor.checkpoint_hash
                            or manifest["checkpoint_step"] != predictor.step):
                        raise ValueError("INCOMPATIBLE_CAPTURE_OR_MODEL")
                    verify_ms += (time.perf_counter_ns()-phase)/1e6
                    shared[sender] = decoded
                    row["received_bevs"][sender] = {
                        "sha256": manifest["sha256"], "actor_id": manifest["actor_id"],
                        "frame_id": manifest["frame_id"], "verification": verify_timing,
                    }
                    row["verified_peer_ids"].append(sender)
                row["stages_ms"].update(receive_wait=wait_ms, verify=verify_ms)
                phase = time.perf_counter_ns()
                row["fusion_mode"] = getattr(predictor,"fusion_mode","weighted")
                fused = fuse_bevs(own, [shared[n] for n in sorted(shared)],mode=row["fusion_mode"])
                evidence_policy = os.environ.get("DAMOS_BEV_EVIDENCE_FUSION", "off")
                if evidence_policy not in ("off", "relative-v3"):
                    raise ValueError("UNKNOWN_EVIDENCE_FUSION_POLICY")
                row["evidence_fusion"] = {"policy": "off"}
                if node == "ego" and evidence_policy == "relative-v3":
                    if row["fusion_mode"] != "ego_observed":
                        raise ValueError("EVIDENCE_FUSION_REQUIRES_EGO_OBSERVED")
                    from bev_lidar_calibration import geometry_features
                    from bev_relative_fusion import PARAMETERS, relative_correction
                    correction_start = time.perf_counter_ns()
                    fused, correction = relative_correction(fused, own,
                        [shared[n] for n in sorted(shared)], arrays["lidar_bev"],
                        geometry_features(arrays["lidar_bev"]))
                    row["evidence_fusion"] = {"policy": evidence_policy, "parameters": PARAMETERS,
                        "relative_promoted_cells": int(correction["relative_promoted"].sum()),
                        "local_promoted_cells": int(correction["strict_flags"]["local_promoted"].sum()),
                        "strong_peer_promoted_cells": int(correction["strict_flags"]["peer_promoted"].sum()),
                        "elapsed_ms": (time.perf_counter_ns()-correction_start)/1e6}
                row["stages_ms"]["fusion"] = (time.perf_counter_ns()-phase)/1e6
                row["status"] = "fusion_complete"
                row["peer_added_cells"] = int(fused["peer_added_cells"].sum())
                row["own_observed_cells"] = int(own["valid"].sum())
                row["fused_observed_cells"] = int(fused["valid"].sum())
            except (TimeoutError, queue.Empty) as exc:
                row.update(status="timeout", error=str(exc) or "MISSING_PEER")
            except Exception as exc:
                row.update(status="error", error=type(exc).__name__+": "+str(exc))
            # Shared process-wide monotonic origin includes IPC dispatch and input copying.
            ended = time.perf_counter_ns()
            row["elapsed_ms"] = (ended-started)/1e6
            row["pipeline_budget_pass"] = row["status"] == "fusion_complete" and row["elapsed_ms"] <= 18000
            if ended > deadline_ns and row["status"] == "fusion_complete":
                row.update(status="timeout", pipeline_budget_pass=False, error="ROUND_DEADLINE")
            row["missing_peer_ids"] = sorted(set(expected)-set(shared))
            # Artifacts and serialization are outside the measured interval.
            try:
                folder = Path(task["save_dir"])
                folder.mkdir(parents=True, exist_ok=True)
                if task.get("save_inputs") and arrays is not None:
                    np.savez_compressed(folder / f"{node}_model_inputs.npz",**arrays)
                if own is not None:
                    np.savez_compressed(folder / f"{node}_own.npz", **own)
                    cv2.imwrite(str(folder / f"{node}_own.png"), render_bev(own["probabilities"], own["valid"]))
                    if "own_bev_sha256" in row:
                        (folder / f"{node}_packet.json").write_text(json.dumps(packet, indent=2))
                if node == "ego" and fused is not None:
                    np.savez_compressed(folder / "ego_fused.npz", **fused)
                    cv2.imwrite(str(folder / "ego_fused.png"), render_bev(fused["probabilities"], fused["valid"]))
                    cv2.imwrite(str(folder / "ego_obstacle_presence.png"),render_presence(fused))
                    display,display_metadata = render_presence_comparison(fused,frame_id=sample["window"][-1])
                    cv2.imwrite(str(folder / "ego_presence_interpolation_comparison.png"),display)
                    (folder / "presence_interpolation.json").write_text(json.dumps(display_metadata,indent=2))
                    for sender, data in shared.items():
                        np.savez_compressed(folder / f"ego_received_{sender}.npz", **data)
                if "packets" in sample:
                    packet = sample["packets"][sample["label"], "cam_front"][-1]
                    bgra = np.frombuffer(packet["raw"], np.uint8).reshape(450,800,4)
                    cv2.imwrite(str(folder / f"{node}_front.png"), bgra[:,:,:3])
                (folder / f"{node}_timing.json").write_text(json.dumps(row, indent=2))
            except Exception as exc:
                row["artifact_error"] = str(exc)
            results.put(row)
    except Exception as exc:
        results.put({"event": "fatal", "node_id": node, "error": type(exc).__name__+": "+str(exc)})
    finally:
        if inbox is not None:
            inbox.close()


class BEVMesh:
    def __init__(self, actor_ids, timeout_s=60):
        if "ego" not in actor_ids or not 2 <= len(actor_ids) <= MAX_NODES:
            raise ValueError("BEV mesh requires ego plus 1..31 observers.")
        self.node_ids, self.timeout_s = tuple(actor_ids), timeout_s
        self.session_id, self.closed = uuid.uuid4().hex, False
        self.processes, self.commands = {}, {}
        ctx = mp.get_context("spawn")
        self.results = ctx.Queue()
        cpus = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
        self.setup = {}
        self.loaded = set()
        try:
            # Sequential loading avoids concurrent checkpoint memory/IO spikes.
            for i, node in enumerate(self.node_ids):
                command = ctx.Queue(maxsize=2)
                affinity = [cpus[(i*2+j) % len(cpus)] for j in range(2)] if cpus else []
                process = ctx.Process(target=_worker, args=(node, command, self.results, timeout_s, affinity))
                process.start()
                self.commands[node], self.processes[node] = command, process
                self.setup.update(self._collect("ready", [node], 180))
            for command in self.commands.values():
                command.put({"session_id": self.session_id, "actor_ids": actor_ids,
                             "addresses": {n: r["address"] for n,r in self.setup.items()},
                             "commitments": {n: r["commitment"] for n,r in self.setup.items()}})
        except BaseException:
            self.close()
            raise

    def _collect(self, event, nodes, timeout_s):
        deadline, rows = time.monotonic()+timeout_s, {}
        while len(rows) < len(nodes):
            if any(not self.processes[n].is_alive() for n in nodes):
                raise RuntimeError("BEV_PEER_EXITED")
            if time.monotonic() >= deadline:
                raise TimeoutError("BEV_PEER_COLLECTION_TIMEOUT")
            try:
                row = self.results.get(timeout=.25)
            except queue.Empty:
                continue
            if row["event"] == "fatal":
                raise RuntimeError(row["node_id"]+": "+row["error"])
            if row["event"] != event or row["node_id"] not in nodes or row["node_id"] in rows:
                raise RuntimeError("UNEXPECTED_BEV_PEER_RESULT")
            rows[row["node_id"]] = row
        return rows

    def warmup(self, tasks):
        for node in sorted(self.loaded-set(tasks)):
            self.commands[node].put({"action": "release"})
            self._collect("release", [node], 30)
            self.loaded.remove(node)
        for node in sorted(set(tasks)-self.loaded):
            self.commands[node].put({"action": "warmup", "input": tasks[node]})
            self.setup[node]["warmup"] = self._collect("warmup", [node], 120)[node]
            self.loaded.add(node)
        if len({self.setup[n]["warmup"]["checkpoint_sha256"] for n in self.loaded}) != 1:
            raise RuntimeError("PEER_MODEL_MISMATCH")

    def exchange(self, tasks, round_id, folder, save_inputs=False):
        if ("ego" not in tasks or len(tasks) < 2 or not set(tasks) <= set(self.node_ids)
                or len({tuple(t["window"]) for t in tasks.values()}) != 1):
            raise ValueError("BEV_REQUIRES_COMMON_FOUR_FRAME_WINDOW")
        graph = {node: ([n for n in sorted(tasks) if n != "ego"] if node == "ego" else ["ego"])
                 for node in tasks}
        started = time.perf_counter_ns()
        for node, sample in tasks.items():
            self.commands[node].put({"action": "round", "input": sample, "graph": graph,
                                     "round_id": round_id, "started_ns": started, "save_dir": str(folder),
                                     "save_inputs":save_inputs})
            # Saving the exact model inputs happens after the worker timer stops.
        rows = self._collect("round", list(tasks), 2*self.timeout_s+30)
        if any(row["round_id"] != round_id for row in rows.values()):
            raise RuntimeError("WRONG_BEV_RESULT_ROUND")
        ego = rows["ego"]
        ego["bidirectional_complete"] = all(r["status"] == "fusion_complete" for r in rows.values())
        ego["bidirectional_elapsed_ms"] = max(r["elapsed_ms"] for r in rows.values())
        if not ego["bidirectional_complete"]:
            ego["pipeline_budget_pass"] = False
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
        for process in self.processes.values():
            process.join(timeout=2)
            if process.is_alive():
                if hasattr(os, "getpgid") and os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                process.join(timeout=3)
                if process.is_alive():
                    process.kill(); process.join(timeout=2)
        for command in self.commands.values():
            command.cancel_join_thread(); command.close()
        self.results.cancel_join_thread(); self.results.close()

    def service_exchange(self, tasks, round_id, folder, faults=None, timeout_s=8.):
        if (set(tasks) != self.loaded or "ego" not in tasks or len(tasks) < 2
                or len({tuple(t["window"]) for t in tasks.values()}) != 1
                or not 0 < timeout_s <= self.timeout_s):
            raise ValueError("Service trial needs common frames and preloaded peers")
        graph = {node: ([n for n in sorted(tasks) if n != "ego"] if node == "ego" else ["ego"])
                 for node in tasks}
        started = time.perf_counter_ns()
        for node, sample in tasks.items():
            self.commands[node].put({"action": "service_round", "input": sample, "graph": graph,
                "round_id": round_id, "started_ns": started,
                "save_dir": str(Path(folder) / node), "service_timeout_s": timeout_s,
                "fault": (faults or {}).get(node, {})})
        return self._collect("service_round", list(tasks), 2 * timeout_s + 30)
