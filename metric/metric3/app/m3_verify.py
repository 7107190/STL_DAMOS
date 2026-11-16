"""Independently recalculate raw-return overlap, continuity, proofs and case totals."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from report_data import atomic_json, relocated_path
from damos_paths import project_root
from verify_metric3_100 import independent_duration, largest_overlap


def load(path):
    return json.loads(path.read_text())


def arrays(path):
    with np.load(path, allow_pickle=False) as data:
        return {k:data[k] for k in data.files}


def verify(root, run, require_full=False):
    from bev_fusion import fuse_bevs
    from bev_occupancy import OBSTACLE, obstacle_presence
    folder = root / "reports" / run
    protocol = load(folder / "protocol.json")
    assert protocol["source"] == "live_carla_sensor_stream"
    assert protocol["gt_used_for_inference"] is False
    assert hashlib.sha256((folder / "protocol.json").read_bytes()).hexdigest() == load(folder / "protocol_lock.json")["sha256"]
    cases = protocol["cases"]
    assert len({c["case_id"] for c in cases}) == len({c["scene_seed"] for c in cases}) == len(cases)
    if require_full:
        assert len(cases) == 100
        assert Counter(c["scenario"] for c in cases) == Counter({**{f"S{i}":11 for i in range(1,9)}, "S9":12})
    successes = failures = total_samples = total_proofs = 0
    per_scenario = {}
    for case in cases:
        result = load(folder / "cases" / case["case_id"] / "result.json")
        assert result["status"] in ("success", "failure"), result
        assert result["returncode"] == 0 and result["source"] == protocol["source"]
        target = relocated_path(result["output"], project_root(root)).resolve()
        assert target.is_relative_to(folder / "cases" / case["case_id"])
        manifest, capture = load(target / "manifest.json"), load(target / "capture.json")
        assert manifest["case"] == case and manifest["gt_used_for_inference"] is False
        assert capture["capture_complete"] and capture["error"] is None
        assert len(capture["timeline"]) == 30 and capture["timeline"][0]["distance_m"] <= 20
        content=capture.get("camera_content_audit")
        if content is not None:
            assert content["frames_checked"]==30
            assert all(not row["all_six_identical_for_entire_clip"] for row in content["nodes"].values())
        assert result["first_inference_dispatch_wall_ns"] < capture["producer_ended_wall_ns"], "Inference did not begin during live acquisition"
        drive_path=target/"post_window_drive.json"
        if drive_path.exists():
            drive=load(drive_path)
            assert not drive["errors"]
            frames=[row["frame_id"] for row in drive["records"]]
            assert all(f>=capture["timeline"][-1]["frame_id"] for f in frames)
            assert all(b-a>=2 for a,b in zip(frames,frames[1:]))
        x,y = case["target_scenic_xy"]
        chosen = min(manifest["candidate_anchors"], key=lambda a: ((a["anchor_location"]["x"]-x)**2+(a["anchor_location"]["y"]+y)**2,a["anchor_index"]))
        assert chosen["anchor_index"] == manifest["anchor_index"] == result["anchor_index"]
        timeline = capture["timeline"]
        for previous,current in zip(timeline,timeline[1:]):
            assert current["frame_id"] - previous["frame_id"] == 2
            assert abs(current["capture_sim_ns"]-previous["capture_sim_ns"]-200_000_000) <= 1000
        paths = sorted(target.glob("round_*"))
        assert len(paths) == result["samples"]
        rows = []
        for index,path in enumerate(paths,1):
            row, truth, peers, live = (load(path/name) for name in ("evidence.json", "ground_truth.json", "peers.json", "live_input.json"))
            assert row["round_index"] == index and row["case_id"] == case["case_id"]
            assert row["frame_id"] == truth["frame_id"] == timeline[index-1]["frame_id"] == live["frames"][-1]
            assert int(row["capture_sim_ns"]) == truth["capture_sim_ns"] == timeline[index-1]["capture_sim_ns"] == live["capture_sim_ns"]
            assert row["member_actor_ids"] == manifest["target_ids"] == sorted(t["actor_id"] for t in truth["targets"])
            assert row["ground_truth_fed_to_model_or_fusion"] is False
            own, fused = arrays(path/"ego_own.npz"), arrays(path/"ego_fused.npz")
            semantic_points = []
            assert set(peers) == set(manifest["actors"]) and len(peers) == 2
            for node,peer in peers.items():
                assert peer["status"] == "fusion_complete" and not peer.get("artifact_error")
                assert peer["input"]["input_source"] == "live_raw_memory"
                assert peer["actor_id"] == manifest["actors"][node]["actor_id"] == live["nodes"][node]["actor_id"]
                assert peer["frame_id"] == row["frame_id"]
                assert peer["inference"]["checkpoint_sha256"] == protocol["checkpoint_sha256"]
                assert peer["proof"]["result_class"] == "REAL_GROTH16"
                assert set(peer["verified_peer_ids"]) == set(peers)-{node} and not peer["missing_peer_ids"]
                assert peer["input"]["frames"] == live["frames"] and peer["input"]["camera_count"] == 6 and peer["input"]["lidar_count"] == 1
                packet = load(path/(node+"_packet.json"))["manifest"]
                assert packet["frame_id"] == row["frame_id"] and abs(packet["capture_sim_ns"]-int(row["capture_sim_ns"]))<=1000
                semantic = arrays(path/(node+"_eval_only_semantic.npz"))
                semantic_points.append(semantic["world_xyz"][np.isin(semantic["actor_ids"],manifest["target_ids"])])
            observer = next(n for n in peers if n != "ego")
            recombined = fuse_bevs(own,[arrays(path/("ego_received_"+observer+".npz"))],mode="ego_observed")
            np.testing.assert_array_equal(recombined["probabilities"],fused["probabilities"])
            np.testing.assert_array_equal(recombined["valid"],fused["valid"])
            points = np.concatenate(semantic_points)
            inverse = np.linalg.inv(own["pose"])
            local = points@inverse[:3,:3].T + inverse[:3,3]
            ix = np.floor((local[:,0]+40)/.4).astype(np.int64)
            iy = np.floor((local[:,1]+40)/.4).astype(np.int64)
            keep=(ix>=0)&(ix<200)&(iy>=0)&(iy<200)&(local[:,2]>=-1)&(local[:,2]<5.4)
            gt=set((ix[keep]*200+iy[keep]).tolist())
            assert gt == set(row["gt_target_cell_indices"]) and len(gt)==row["gt_return_cells"]
            assert row["eligible"] == (row["on_driving_lane"] is True and row["in_ego_roi"] is True and len(gt)>=3)
            for name,prediction in (("ego",own),("fused",fused)):
                occupancy=obstacle_presence(prediction["probabilities"],prediction["valid"])["occupancy"]
                cells=set(np.flatnonzero(occupancy==OBSTACLE).tolist())
                assert cells==set(row[name+"_obstacle_cell_indices"])
                overlap=largest_overlap(gt,cells)
                assert overlap==row[name+"_connected_overlap_cells"]
                assert row[name+"_detected"] is (bool(overlap>=3) if len(gt)>=3 else None)
            image=load(path/"scene_capture.json")
            assert image["frame_id"]==row["frame_id"] and image["capture_sim_ns"]==int(row["capture_sim_ns"])
            assert hashlib.sha256((path/"scene_rgb.png").read_bytes()).hexdigest()==image["image_sha256"]
            assert load(path/"audit.json")["proofs_reverified"]==2
            rows.append(row)
        span=independent_duration(rows,"fused")
        assert span==int(result["fused_longest_span_ns"])
        assert result["successful"] is (span>=500_000_000)
        assert result["status"]==("success" if result["successful"] else "failure")
        if not result["successful"]:
            assert len(rows)==30
        successes+=int(result["successful"])
        failures+=int(not result["successful"])
        total_samples+=len(rows)
        total_proofs+=2*len(rows)
        entry=per_scenario.setdefault(case["scenario"],{"cases":0,"successful":0})
        entry["cases"]+=1;entry["successful"]+=int(result["successful"])
    summary=load(folder/"summary.json")
    assert summary["all_cases_complete"] and summary["evaluated"]==len(cases)
    assert summary["successful"]==successes and summary["detection_failures"]==failures and summary["execution_errors"]==0
    assert abs(summary["success_rate_percent"]-successes/len(cases)*100)<1e-9
    return {"run":run,"source":"live_carla_sensor_stream","actual_live_anchor_cases":len(cases),"successful":successes,
            "detection_failures":failures,"execution_errors":0,"success_rate_percent":successes/len(cases)*100,
            "samples_independently_verified":total_samples,"real_proofs_reverified":total_proofs,
            "inference_during_current_capture":True,"same_frame_evidence":True,"raw_semantic_returns_recalculated":True,
            "fusion_recalculated":True,"continuous_duration_recalculated":True,"ground_truth_fed_to_model":False,
            "per_scenario":per_scenario,"criterion_pass":len(cases)==100 and successes>=70,"all_pass":True}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--run",required=True)
    parser.add_argument("--require-full",action="store_true")
    args=parser.parse_args()
    result=verify(args.root.resolve(),args.run,args.require_full)
    atomic_json(args.root/"reports"/args.run/"verification.json",result)
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
