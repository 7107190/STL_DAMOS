"""Immutable replay of verified BEVs; experimental fusion never consumes GT."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from bev_anchor_evaluation import region_presence
from bev_evaluation import observed_presence_ground_truth, points_mask
from bev_fusion import fuse_bevs, warp_to_ego
from bev_model_runtime import CLASS_NAMES
from bev_occupancy import OBSTACLE, obstacle_presence, render_presence, score_scene_presence


def load_npz(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def scene_scores(predictions, evidence, truth):
    pose = predictions["ego"]["pose"]
    gt, gt_valid = observed_presence_ground_truth(evidence, pose)
    rows = {}
    for name, prediction in predictions.items():
        anchors = []
        groups = {}
        for target in truth["targets"]:
            groups.setdefault((target["scenario"],target["anchor_index"]),[]).append(target["actor_id"])
        for (scenario,index), members in sorted(groups.items()):
            mask = np.zeros((200,200),bool)
            for xyz, ids, _ in evidence.values():
                mask |= points_mask(xyz[np.isin(ids,members)],pose)
            score = region_presence(prediction,mask)
            anchors.append(dict(score,scenario=scenario,anchor_index=index,member_actor_ids=members))
        rows[name] = {"anchors":anchors,"scene":score_scene_presence(prediction,gt,gt_valid)}
    return rows


def conflict_rows(ego, peers, baseline, candidate):
    own_occ = obstacle_presence(ego["probabilities"],ego["valid"])["occupancy"]
    old_occ = obstacle_presence(baseline["probabilities"],baseline["valid"])["occupancy"]
    lost = (own_occ==OBSTACLE)&(old_occ!=OBSTACLE)
    warped = [warp_to_ego(peer,ego["pose"]) for peer in peers]
    rows = []
    for x,y in np.argwhere(lost):
        sources = []
        for name,p,valid in [("ego",ego["probabilities"],ego["valid"]),
                             *[(f"peer_{i}",p,m) for i,(p,m) in enumerate(warped)]]:
            if not valid[x,y]:
                continue
            cls = int(p[:,x,y].argmax())
            sources.append({"source":name,"class":CLASS_NAMES[cls],"max_probability":float(p[cls,x,y])})
        rows.append({"grid_xy":[int(x),int(y)],"sources":sources,
                     "weighted_class":CLASS_NAMES[int(baseline["probabilities"][:,x,y].argmax())],
                     "ego_observed_class":CLASS_NAMES[int(candidate["probabilities"][:,x,y].argmax())]})
    return rows


def score_hidden_parts(predictions, evidence, evaluation):
    scores = {name:[] for name in predictions}
    for anchor in evaluation["anchors"]:
        members = [obj["actor_id"] for obj in evaluation["objects"] if obj["occlusion_verified"]
                   and obj["actor_id"] in anchor["member_actor_ids"]]
        mask = np.zeros((200,200),bool)
        for xyz,ids,_ in evidence.values():
            mask |= points_mask(xyz[np.isin(ids,members)],predictions["ego"]["pose"])
        for name,prediction in predictions.items():
            scores[name].append(dict(region_presence(prediction,mask),scenario=anchor["scenario"],anchor_index=anchor["anchor_index"]))
    return scores


def comparison_image(predictions, mask, title):
    indices = np.argwhere(mask)
    width = min(200,max(24,int(np.ptp(indices,axis=0).max())+12)) if len(indices) else 200
    centre = indices.mean(0).astype(int) if len(indices) else np.array([100,100])
    x0,y0 = np.clip(centre-width//2,0,200-width)
    panels = []
    for name,prediction in predictions.items():
        image = render_presence(prediction,200)[200-x0-width:200-x0,200-y0-width:200-y0]
        image = cv2.resize(image,(380,380),interpolation=cv2.INTER_NEAREST)
        outline = cv2.resize(mask[x0:x0+width,y0:y0+width][::-1,::-1].astype(np.uint8),(380,380),interpolation=cv2.INTER_NEAREST)
        contours,_ = cv2.findContours(outline,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(image,contours,-1,(255,255,255),1)
        header = np.full((64,380,3),20,np.uint8)
        score = region_presence(prediction,mask)
        cv2.putText(header,name,(10,24),cv2.FONT_HERSHEY_SIMPLEX,.62,(240,240,240),1)
        cv2.putText(header,f"Overlap={score['obstacle_overlap_cells']} | connected={score['largest_connected_overlap_cells']}",
                    (10,50),cv2.FONT_HERSHEY_SIMPLEX,.48,(240,240,240),1)
        panels.append(np.vstack([header,image]))
    header = np.full((78,380*len(panels),3),20,np.uint8)
    cv2.putText(header,title,(12,27),cv2.FONT_HERSHEY_SIMPLEX,.7,(240,240,240),1)
    cv2.putText(header,"Orange=obstacle; gray=ground; white=GT outline only. Same raw frame, no interpolation.",
                (12,58),cv2.FONT_HERSHEY_SIMPLEX,.55,(240,240,240),1)
    return np.vstack([header,np.hstack(panels)])


def summarize(rows):
    result = {}
    for name in ("ego","weighted","ego_observed"):
        anchors = [a for row in rows for a in row["scores"][name]["anchors"]]
        scenes = [row["scores"][name]["scene"] for row in rows]
        fp = sum(s["ground_false_positive_cells"] for s in scenes)
        tn = sum(s["true_ground_cells"] for s in scenes)
        result[name] = {"region_frames_assessable":sum(a["assessable"] for a in anchors),
                        "region_frames_detected":sum(a["detected"] is True for a in anchors),
                        "ground_false_positive_cells":fp,"true_ground_cells":tn,
                        "ground_false_positive_rate_on_decided":fp/(fp+tn) if fp+tn else None}
        hidden = [a for row in rows for a in row.get("hidden_scores",{}).get(name,[])]
        result[name]["hidden_region_frames_assessable"] = sum(a["assessable"] for a in hidden)
        result[name]["hidden_region_frames_detected"] = sum(a["detected"] is True for a in hidden)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-summary",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    rows = []
    hashes = {str(args.source_summary):hashlib.sha256(args.source_summary.read_bytes()).hexdigest()}
    representatives = set()
    source = json.loads(args.source_summary.read_text())
    for run in source["rows"]:
        for folder in sorted(Path(run["report"]).glob("round_*")):
            if not (folder/"evaluation.json").exists():
                continue
            paths = [folder/"ego_own.npz",folder/"ego_fused.npz",folder/"ground_truth.json",folder/"evaluation.json",
                     *sorted(folder.glob("*_packet.json")),
                     *sorted(folder.glob("ego_received_*.npz")),*sorted(folder.glob("*_eval_only_semantic.npz"))]
            for path in paths:
                hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            own = load_npz(folder/"ego_own.npz")
            peers = [load_npz(path) for path in sorted(folder.glob("ego_received_*.npz"))]
            weighted = fuse_bevs(own,peers)
            saved = load_npz(folder/"ego_fused.npz")
            if not np.array_equal(weighted["probabilities"],saved["probabilities"]):
                raise ValueError("Baseline replay differs from immutable saved result.")
            candidate = fuse_bevs(own,peers,mode="ego_observed")
            predictions = {"ego":own,"weighted":weighted,"ego_observed":candidate}
            evidence = {}
            for path in folder.glob("*_eval_only_semantic.npz"):
                data = load_npz(path)
                evidence[path.stem] = (data["world_xyz"],data["actor_ids"],data["tags"])
            truth = json.loads((folder/"ground_truth.json").read_text())
            scores = scene_scores(predictions,evidence,truth)
            conflicts = conflict_rows(own,peers,weighted,candidate)
            row = {"scenario":run["scenario"],"folder":str(folder),"frame_id":truth["frame_id"],
                   "scores":scores,"ego_obstacle_cells_erased_by_weighted":len(conflicts),"conflicts":conflicts}
            evaluation = json.loads((folder/"evaluation.json").read_text())
            row["hidden_scores"] = score_hidden_parts(predictions,evidence,evaluation)
            rows.append(row)
            if run["scenario"] in ("S1","S8"):
                for i,anchor in enumerate(scores["ego"]["anchors"]):
                    regression = anchor["detected"] is True and scores["weighted"]["anchors"][i]["detected"] is False
                    key = run["scenario"]
                    if key in representatives or not anchor["assessable"] or (key=="S8" and not regression):
                        continue
                    mask = np.zeros((200,200),bool)
                    for xyz,ids,_ in evidence.values():
                        mask |= points_mask(xyz[np.isin(ids,anchor["member_actor_ids"])],own["pose"])
                    cv2.imwrite(str(args.output/f"{key}_fusion_comparison.png"),comparison_image(
                        predictions,mask,f"{key} anchor {anchor['anchor_index']} F{truth['frame_id']} | frozen replay; experimental candidate"))
                    representatives.add(key)
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in hashes.items())
    report = {"experimental":True,"production_default":"weighted; unchanged","source_files_unchanged":unchanged,
              "scope":"Development replay, not independent validation or new 18s measurement. GT scores only, never fusion input.",
              "candidate":"ego_observed: preserve all ego-observed distributions, fill only unobserved cells with weighted peers",
              "aggregate":summarize(rows),"per_scenario":{s:summarize([r for r in rows if r['scenario']==s]) for s in sorted({r['scenario'] for r in rows})},
              "rows":rows,"source_sha256":hashes,"metric2_pass":None}
    (args.output/"summary.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({k:report[k] for k in ("aggregate","per_scenario","source_files_unchanged")},indent=2),flush=True)
    if not unchanged:
        raise RuntimeError("Source changed during diagnostic.")


if __name__=="__main__":
    main()
