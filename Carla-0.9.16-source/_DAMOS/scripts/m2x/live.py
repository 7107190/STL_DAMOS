"""Live CARLA front-camera adapter and ego-only timing reports."""
from collections import OrderedDict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import threading
import time

from .mesh import PeerMesh, neighbour_graph


class FrameBuffer:
    def __init__(self, node_ids, capacity=4):
        self.frames = {node: OrderedDict() for node in node_ids}
        self.received = {node: 0 for node in node_ids}
        self.dropped = {node: 0 for node in node_ids}
        self.capacity = capacity
        self.lock = threading.Lock()

    def push(self, node, sample):
        with self.lock:
            frames = self.frames[node]
            frames[sample["frame_id"]] = sample
            self.received[node] += 1
            while len(frames) > self.capacity:
                frames.popitem(last=False)
                self.dropped[node] += 1

    def take_latest(self, after_frame=-1, node_ids=None):
        with self.lock:
            selected = self.frames.keys() if node_ids is None else node_ids
            common = set.intersection(*(set(self.frames[node]) for node in selected))
            common = {frame for frame in common if frame > after_frame}
            if not common:
                return None
            frame = max(common)
            samples = {node: self.frames[node][frame] for node in selected}
            for node in selected:
                rows = self.frames[node]
                for old in list(rows):
                    if old <= frame:
                        del rows[old]
                        self.dropped[node] += int(old != frame)
            return samples

    def latest(self, node):
        with self.lock:
            rows = self.frames[node]
            return rows[max(rows)] if rows else None

    def clear(self, node):
        with self.lock:
            self.dropped[node] += len(self.frames[node])
            self.frames[node].clear()

    def stats(self):
        with self.lock:
            return {node: {"received": self.received[node], "dropped": self.dropped[node],
                           "buffered": len(rows)} for node, rows in self.frames.items()}


def write_summary(folder, records, **extra):
    successful = [row["elapsed_ms"] for row in records
                  if row["status"] == "sharing_complete_detection_pending"]
    summary = {
        "evaluation_target": "ego", "backend": "REAL_GROTH16",
        "deployment": "single_host_independent_processes_direct_tcp_loopback",
        "topology": "ego_star_no_observer_to_observer",
        "detector_status": "not_connected", "metric2_pass": None,
        "path_planning": "excluded", "target_ms": 18000,
        "measurement": "ego input accepted -> encode/prove/send/receive/verify/decode complete",
        "excluded": ["sensor acquisition wait", "worker/key setup", "report/PNG writes",
                     "missing detector", "path planning"],
        "rounds_recorded": len(records), "sharing_successes": len(successful),
        "sharing_failures": len(records) - len(successful),
        "sharing_mean_ms": statistics.mean(successful) if successful else None,
        "sharing_max_ms": max(successful) if successful else None,
        **extra,
    }
    (Path(folder) / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def camera_positions(world, nodes):
    """Discovery uses CARLA poses, not images or a simulated wireless beacon protocol."""
    import carla
    snapshot = world.get_snapshot()
    positions = {}
    for node, info in nodes.items():
        actor = snapshot.find(info["actor_id"])
        if actor is None:
            if node == "ego":
                raise RuntimeError("PINNED_EGO_DISAPPEARED")
            continue
        transform = actor.get_transform()
        relative = info["relative_transform"]["location"]
        point = transform.transform(carla.Location(**relative))
        positions[node] = {"position": [point.x, point.y, point.z],
                           "yaw": transform.rotation.yaw}
    return positions, snapshot.frame


class FrontSensors:
    def __init__(self, world, nodes, buffer):
        self.world, self.nodes, self.buffer = world, nodes, buffer
        self.listening = {}

    def set_active(self, selected):
        selected = set(selected)
        for node in set(self.listening) - selected:
            sensor = self.listening.pop(node)
            try:
                sensor.stop()
            except RuntimeError:
                pass
            self.buffer.clear(node)
        for node in selected - set(self.listening):
            info = self.nodes[node]
            sensor = self.world.get_actor(info["sensor_actor_id"])
            if sensor is None:
                raise RuntimeError("FRONT_CAMERA_DISAPPEARED:" + node)
            self.buffer.clear(node)

            def on_image(image, node=node, actor_id=info["actor_id"]):
                position = image.transform.location
                self.buffer.push(node, {"frame_id": image.frame, "actor_id": actor_id,
                                       "capture_sim_ns": round(image.timestamp * 1e9),
                                       "width": image.width, "height": image.height,
                                       "position": [position.x, position.y, position.z],
                                       "raw": bytes(image.raw_data)})

            sensor.listen(on_image)
            self.listening[node] = sensor


class SharingState:
    def __init__(self):
        self.lock = threading.Lock()
        self.value = {"phase": "starting", "selected": [], "round_id": 0, "last_result": None}

    def update(self, **values):
        with self.lock:
            self.value.update(values)

    def snapshot(self):
        with self.lock:
            return dict(self.value)


def run_live_sharing(world, attachments, report_dir, *, rounds=5, radius_m=60.0,
                     timeout_s=18.0, duration_s=0.0, save_video=False,
                     scenic_proc=None, scenario_labels=(), on_tick=None, logger=print):
    front = [row for row in attachments if row["sensor_name"] == "cam_front"]
    nodes = {("ego" if row["track_label"] == "ego" else f"observer_{row['actor_id']}"): row
             for row in front}
    if len(nodes) != len(front):
        raise ValueError("DUPLICATE_FRONT_CAMERAS")
    folder = Path(report_dir) / ("live_sharing_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))
    folder.mkdir(parents=True, exist_ok=False)
    logger(f"[M2X] Starting {len(nodes)} independent peers; report={folder}")
    buffer = FrameBuffer(nodes)
    mesh, recorder, records = None, None, []
    sensors = FrontSensors(world, nodes, buffer)
    state = SharingState()
    changes, idle_polls, idle_seconds = [], 0, 0.0
    video_result = None
    failure = None
    started = time.monotonic()
    end_reason = "error"
    try:
        mesh = PeerMesh(tuple(nodes), timeout_s=timeout_s)
        sensors.set_active(["ego"])
        if save_video:
            from .video import SharingVideo
            recorder = SharingVideo(world, nodes, buffer, state, folder, radius_m, scenario_labels)
            recorder.start()
        session_start = time.monotonic()
        wall_limit = duration_s if duration_s > 0 else 120 + rounds * (3 * timeout_s + 10)
        deadline = session_start + wall_limit
        last_frame, round_id, previous_peers = -1, 0, None

        def membership(selected, frame):
            nonlocal previous_peers
            selected = sorted(selected)
            if selected != previous_peers:
                event = {"wall_seconds": time.monotonic() - session_start, "frame_id": frame,
                         "selected": selected, "entered": sorted(set(selected) - set(previous_peers or [])),
                         "left": sorted(set(previous_peers or []) - set(selected))}
                changes.append(event)
                with (folder / "membership.jsonl").open("a") as output:
                    output.write(json.dumps(event) + "\n")
                logger(f"[M2X] Nearby: {', '.join(selected) or 'none; ego keeps driving'} "
                       f"entered={event['entered']} left={event['left']}")
                previous_peers = selected

        while time.monotonic() < deadline and (rounds == 0 or round_id < rounds):
            if scenic_proc is not None and scenic_proc.poll() is not None:
                raise RuntimeError("SCENIC_ENDED_DURING_LIVE_SHARING")
            if on_tick is not None:
                on_tick()
            positions, frame = camera_positions(world, nodes)
            nearby = neighbour_graph(positions, radius_m)["ego"]
            active = ["ego", *nearby]
            membership(nearby, frame)
            sensors.set_active(active)
            state.update(phase="acquiring" if nearby else "idle", selected=nearby)
            if not nearby:
                idle_start = time.monotonic()
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
                idle_seconds += time.monotonic() - idle_start
                idle_polls += 1
                continue
            acquisition_start = time.monotonic()
            samples = None
            while time.monotonic() < deadline:
                samples = buffer.take_latest(last_frame, active)
                if samples is not None:
                    break
                if scenic_proc is not None and scenic_proc.poll() is not None:
                    raise RuntimeError("SCENIC_ENDED_BEFORE_NEXT_FRONT_FRAME")
                if time.monotonic() - acquisition_start > timeout_s:
                    raise TimeoutError("SAME_FRAME_ACQUISITION_TIMEOUT")
                positions, _ = camera_positions(world, nodes)
                if neighbour_graph(positions, radius_m)["ego"] != nearby:
                    break
                time.sleep(0.05)
            if samples is None:
                continue
            # Recheck at the actual shared frame. The participant set is fixed for this round.
            nearby = neighbour_graph(samples, radius_m)["ego"]
            last_frame = samples["ego"]["frame_id"]
            membership(nearby, last_frame)
            if not nearby:
                continue
            samples = {node: samples[node] for node in ["ego", *nearby]}
            acquisition_ms = (time.monotonic() - acquisition_start) * 1000
            round_id += 1
            state.update(phase="exchanging", selected=nearby, round_id=round_id, exchange_frame=last_frame)
            rows = mesh.exchange(samples, round_id, radius_m,
                                 save_dir=folder / f"round_{round_id:03d}")
            ego = rows["ego"]
            reciprocal = [node for node in nearby
                          if rows[node]["status"] == "sharing_complete_detection_pending"
                          and rows[node]["verified_peer_ids"] == ["ego"]]
            ego["reciprocal_verified_peer_ids"] = sorted(reciprocal)
            ego["bidirectional_complete"] = len(reciprocal) == len(nearby)
            if not ego["bidirectional_complete"] and ego["status"] == "sharing_complete_detection_pending":
                ego.update(status="peer_verification_failed", transport_budget_pass=False)
            ego["peer_distances_m"] = {node: sum((a-b)**2 for a, b in zip(
                samples["ego"]["position"], samples[node]["position"]))**0.5 for node in nearby}
            ego["ego_position"] = samples["ego"]["position"]
            ego["acquisition_wait_ms_excluded"] = acquisition_ms
            records.append(ego)
            last_frame = samples["ego"]["frame_id"]
            with (folder / "ego_rounds.jsonl").open("a") as output:
                output.write(json.dumps(ego) + "\n")
            with (folder / "peer_debug.jsonl").open("a") as output:
                for row in rows.values():
                    output.write(json.dumps(row) + "\n")
            state.update(phase="verified" if ego["bidirectional_complete"] else "failed", last_result=ego)
            logger(f"[M2X] {round_id}/{rounds or 'time-limited'} frame={last_frame} "
                   f"verified={len(ego['verified_peer_ids'])}/{len(ego['peer_ids'])} "
                   f"sharing={ego['elapsed_ms']/1000:.3f}s status={ego['status']} "
                   "detector=not_connected (18s full metric: not evaluated)")
            if "error" in ego:
                logger(f"[M2X] {ego['error']}")
        end_reason = "round_limit" if rounds > 0 and round_id >= rounds else "duration_limit"
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if recorder is not None:
            video_result = recorder.close()
        sensors.set_active([])
        if mesh is not None:
            mesh.close()
        summary = write_summary(
            folder, records, source="live_carla", scenario_labels=list(scenario_labels),
            rounds_requested=rounds, radius_m=radius_m, timeout_s=timeout_s,
            duration_seconds=duration_s, end_reason=end_reason,
            idle_polls=idle_polls, idle_seconds=idle_seconds, membership_changes=changes,
            video=video_result,
            session_id=mesh.session_id if mesh else None, nodes=nodes,
            frame_buffer=buffer.stats(), fatal_error=failure,
            run_wall_seconds=time.monotonic() - started,
        )
        logger(f"[M2X] Summary: {folder / 'summary.json'}")
    if video_result and video_result.get("error"):
        raise RuntimeError("Video recording failed: " + video_result["error"])
    if summary["sharing_failures"]:
        raise RuntimeError(f"M2X incomplete: {summary['sharing_successes']}/{rounds}; see {folder}")
    if not records:
        logger("[M2X] No neighbours encountered; idle session ended normally. No performance pass claimed.")
    return summary
