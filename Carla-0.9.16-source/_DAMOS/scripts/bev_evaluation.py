"""Evaluation-only, actor-ID-exact semantic returns and same-frame CARLA boxes."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np

from bev_model_runtime import CLASS_NAMES, COLORS, render_bev
from bev_anchor_evaluation import evaluate_anchors
from bev_occupancy import (GROUND,OBSTACLE,render_presence,render_presence_comparison,
                           score_presence,score_scene_presence)

SEMANTIC_DTYPE = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                           ("cos", "<f4"), ("actor_id", "<u4"), ("tag", "<u4")])
OBSTACLE_TAGS = (3,4,5,6,7,8,9,12,13,14,15,16,17,18,19,20,21,26,28)
GROUND_TAGS = (1,2,10,23,24,25,27)


def observed_presence_ground_truth(evidence, pose):
    obstacle,ground = np.zeros((200,200),bool),np.zeros((200,200),bool)
    for xyz,_,tags in evidence.values():
        obstacle |= points_mask(xyz[np.isin(tags,OBSTACLE_TAGS)],pose)
        ground |= points_mask(xyz[np.isin(tags,GROUND_TAGS)],pose)
    gt = np.zeros((200,200),np.uint8)
    gt[ground],gt[obstacle] = GROUND,OBSTACLE
    return gt,ground|obstacle


def decode_instance_cameras(cameras, frame_id):
    decoded = {}
    for name,packet in cameras.items():
        if packet["frame_id"]!=frame_id:
            raise ValueError("Instance camera frame mismatch.")
        pixels = np.frombuffer(packet["raw"],np.uint8).reshape(packet["height"],packet["width"],4)
        # CARLA Tagger::GetLabelColor: R=semantic, G=ID low, B=ID high.
        decoded[name] = {"ids":pixels[:,:,1].astype(np.uint16)+pixels[:,:,0].astype(np.uint16)*256,
                         "tags":pixels[:,:,2].copy()}
    return decoded


def camera_projection_counts(folder, node, world_points, pose, frame_id):
    from bev_sensor_rig import CAMERAS
    path = folder/f"{node}_model_inputs.npz"
    if not path.exists():
        return None
    points = local_points(world_points,pose)
    counts = {}
    with np.load(path,allow_pickle=False) as data:
        if int(data["frame_ids"][-1])!=frame_id:
            raise ValueError("Visibility input frame mismatch.")
        for k,name in enumerate(CAMERAS):
            optical = (points-data["trans"][0,-1,k])@data["rots"][0,-1,k]
            projected = optical@data["intrins"][0,-1,k].T
            uv = projected[:,:2]/np.maximum(projected[:,2:3],1e-8)
            counts[name] = int(((optical[:,2]>.1)&(uv[:,0]>=0)&(uv[:,0]<800)&(uv[:,1]>=0)&(uv[:,1]<450)).sum())
    return counts


def target_class(type_id):
    if type_id.startswith("walker.pedestrian."):
        return 7
    if any(token in type_id for token in ("diamondback", "gazelle", "bh.crossbike")):
        return 2
    if any(token in type_id for token in ("harley", "kawasaki", "yamaha", "vespa")):
        return 6
    if type_id.startswith("vehicle."):
        return 4
    if type_id.startswith("static.prop."):
        return 1 if "barrier" in type_id or "fence" in type_id else 8
    raise ValueError("Unmapped evaluation target: " + type_id)


def decode_semantic(packet):
    if len(packet["raw"]) % SEMANTIC_DTYPE.itemsize:
        raise ValueError("Malformed semantic LiDAR bytes.")
    points = np.frombuffer(packet["raw"], SEMANTIC_DTYPE)
    xyz = np.column_stack([points[n] for n in ("x", "y", "z")])
    if not np.isfinite(xyz).all():
        raise ValueError("Nonfinite semantic LiDAR.")
    matrix = np.asarray(packet["transform"])
    world = xyz @ matrix[:3,:3].T + matrix[:3,3]
    world[:,1] *= -1
    return world, points["actor_id"], points["tag"]


def local_points(points, pose):
    inv = np.linalg.inv(pose)
    return np.asarray(points) @ inv[:3,:3].T + inv[:3,3]


def points_mask(world_points, pose):
    mask = np.zeros((200,200), bool)
    p = local_points(world_points, pose)
    ix = np.floor((p[:,0]+40)/.4).astype(np.int64)
    iy = np.floor((p[:,1]+40)/.4).astype(np.int64)
    ok = (ix>=0)&(ix<200)&(iy>=0)&(iy<200)&(p[:,2]>=-1)&(p[:,2]<5.4)
    mask[ix[ok],iy[ok]] = True
    return mask


def footprint_mask(vertices_world, pose):
    xy = local_points(vertices_world, pose)[:,:2].astype(np.float32)
    hull = cv2.convexHull(xy).reshape(-1,2)
    axis = -40+(np.arange(200)+.5)*.4
    x,y = np.meshgrid(axis,axis,indexing="ij")
    cross = []
    for a,b in zip(hull,np.roll(hull,-1,axis=0)):
        cross.append((b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0]))
    return (np.all(np.asarray(cross)>=-1e-6,axis=0) |
            np.all(np.asarray(cross)<=1e-6,axis=0)) if len(hull)>=3 else np.zeros((200,200),bool)


def target_roi(target, pose, returns):
    """New captures use snapshot centres; legacy boxes remain diagnostic only."""
    if "location_model_world" in target:
        point = local_points(np.asarray([target["location_model_world"]]), pose)[0]
        inside = bool(np.all(point[:2]>=-40) and np.all(point[:2]<40))
        return bool(returns.any() or inside), None, "snapshot centre or exact return in ROI"
    footprint = footprint_mask(target["vertices_model_world"], pose)
    return bool(returns.any() or footprint.any()), footprint, "legacy bbox or exact return in ROI"


def score_prediction(prediction, truth_mask, expected_class):
    observed = truth_mask & prediction["valid"].astype(bool)
    labels = prediction["probabilities"].argmax(0)
    correct = observed & (labels == expected_class)
    tolerant = cv2.dilate(((labels==expected_class)&prediction["valid"].astype(bool)).astype(np.uint8),
                          np.ones((3,3),np.uint8)).astype(bool) & truth_mask
    total = int(truth_mask.sum())
    counts = np.bincount(labels[observed],minlength=17)
    fraction = int(correct.sum())/total if total else None
    return {"gt_return_cells": total, "predicted_cells": int(observed.sum()),
            "correct_cells": int(correct.sum()), "correct_fraction": fraction,
            "correct_cells_with_0_4m_tolerance":int(tolerant.sum()),
            "correct_fraction_with_0_4m_tolerance":int(tolerant.sum())/total if total else None,
            "dominant_prediction": CLASS_NAMES[int(counts.argmax())] if observed.any() else "unknown",
            "prediction_counts": {CLASS_NAMES[i]:int(n) for i,n in enumerate(counts) if n},
            "diagnostic_match": bool(total>=3 and fraction>=.5),
            "mean_expected_probability": float(prediction["probabilities"][expected_class][truth_mask].mean()) if total else None}


class SemanticEvaluationRig:
    def __init__(self, rig, anchor_assignments, *, instance_cameras=True):
        import carla
        from bev_sensor_rig import CAMERAS
        self.world, self.lock = rig.world, threading.Lock()
        self.sensors, self.buffers, self.snapshots, self.errors = [], {}, {}, []
        self.instance_buffers = {}
        self.ego_id = rig.actors["ego"].id
        self.map = self.world.get_map()
        self.driving_lane_type = carla.LaneType.Driving
        self.ego_trace = []
        self.initial_target_locations = {}
        self.targets = {}
        self.callback_id = None
        for assignment in anchor_assignments:
            for member in assignment["member_actors"]:
                actor_id = member["actor_id"]
                if actor_id in self.targets:
                    continue
                actor = self.world.get_actor(actor_id)
                if actor is None:
                    raise RuntimeError(f"Pinned evaluation target disappeared: {actor_id}")
                cls = target_class(actor.type_id)
                initial = actor.get_transform().location
                self.initial_target_locations[actor_id] = np.array([initial.x,initial.y,initial.z])
                self.targets[actor_id] = {"actor_id":actor_id, "type_id":actor.type_id,
                    "scenario":member["scenario_label"], "anchor_index":assignment["anchor_index"],
                    "expected_class":cls, "expected_name":CLASS_NAMES[cls]}
        if not self.targets:
            raise ValueError("No pinned abnormal targets for BEV evaluation.")
        if any(actor_id>=65536 for actor_id in self.targets):
            raise ValueError("Instance camera actor ID exceeds its 16-bit encoding.")
        registered_ids = [actor.id&65535 for actor in self.world.get_actors()]
        if len(set(registered_ids))!=len(registered_ids):
            raise ValueError("Ambiguous 16-bit instance actor IDs.")
        try:
            self.callback_id = self.world.on_tick(self._on_tick)
            for label,actor in rig.actors.items():
                self.buffers[label] = {}
                original = next(r for r in rig.records if r["track_label"]==label and r["sensor_name"]=="lidar")
                transform = original["relative_transform"]
                mount = carla.Transform(carla.Location(**transform["location"]), carla.Rotation(**transform["rotation"]))
                bp = self.world.get_blueprint_library().find("sensor.lidar.ray_cast_semantic")
                for key,value in {"channels":"64", "range":"80", "points_per_second":"1300000",
                        "rotation_frequency":str(1/rig.step), "upper_fov":"7", "lower_fov":"-16",
                        "sensor_tick":"0.0", "role_name":f"damos_eval_only_{actor.id}"}.items():
                    if bp.has_attribute(key):
                        bp.set_attribute(key,value)
                sensor = self.world.spawn_actor(bp,mount,attach_to=actor,attachment_type=carla.AttachmentType.Rigid)
                self.sensors.append(sensor)
                sensor.listen(lambda data,label=label:self._receive(label,data))
                self.instance_buffers[label] = {}
                for record in rig.records:
                    if not instance_cameras or record["track_label"]!=label or record["sensor_name"] not in CAMERAS:
                        continue
                    name = record["sensor_name"]
                    self.instance_buffers[label][name] = {}
                    tr = record["relative_transform"]
                    mount = carla.Transform(carla.Location(**tr["location"]),carla.Rotation(**tr["rotation"]))
                    bp = self.world.get_blueprint_library().find("sensor.camera.instance_segmentation")
                    for key in ("image_size_x","image_size_y","fov"):
                        bp.set_attribute(key,str(record["attributes"][key]))
                    bp.set_attribute("sensor_tick","0.0")
                    camera = self.world.spawn_actor(bp,mount,attach_to=actor,attachment_type=carla.AttachmentType.Rigid)
                    self.sensors.append(camera)
                    camera.listen(lambda data,label=label,name=name:self._receive_instance(label,name,data))
        except BaseException:
            self.close()
            raise

    def _on_tick(self, snapshot):
        try:
            records = []
            for actor_id, metadata in self.targets.items():
                state = snapshot.find(actor_id)
                if state is None:
                    raise RuntimeError(f"Missing target {actor_id} in frame {snapshot.frame}")
                matrix = np.asarray(state.get_transform().get_matrix())
                velocity = state.get_velocity()
                location = matrix[:3,3] * np.array([1.,-1.,1.])
                records.append(dict(metadata,location_model_world=location.tolist(),
                    geometry_source="same-frame actor snapshot; no bbox RPC",
                    displacement_since_eval_start_m=float(np.linalg.norm(
                        matrix[:2,3]-self.initial_target_locations[actor_id][:2])),
                    speed_mps=float(np.linalg.norm([velocity.x,velocity.y,velocity.z]))))
            ego = snapshot.find(self.ego_id)
            if ego is None:
                raise RuntimeError("Pinned ego missing from evaluation snapshot.")
            transform,velocity = ego.get_transform(),ego.get_velocity()
            location = transform.location
            waypoint = self.map.get_waypoint(location,project_to_road=False,lane_type=self.driving_lane_type)
            road = {"frame_id":snapshot.frame,"capture_sim_ns":round(snapshot.timestamp.elapsed_seconds*1e9),
                "actor_id":self.ego_id,"location_carla":[location.x,location.y,location.z],
                "speed_mps":float(np.linalg.norm([velocity.x,velocity.y,velocity.z])),
                "on_driving_lane":waypoint is not None,
                "road_id":waypoint.road_id if waypoint else None,
                "lane_id":waypoint.lane_id if waypoint else None,
                "is_junction":waypoint.is_junction if waypoint else None}
            with self.lock:
                self.ego_trace.append(road)
                self.snapshots[snapshot.frame] = {"frame_id":snapshot.frame,
                    "capture_sim_ns":round(snapshot.timestamp.elapsed_seconds*1e9), "targets":records,
                    "ego_road_state":road}
                for frame in sorted(self.snapshots)[:-24]:
                    del self.snapshots[frame]
        except Exception as exc:
            with self.lock:
                self.errors.append(str(exc))

    def _receive(self, label, data):
        packet = {"raw":bytes(data.raw_data), "transform":np.asarray(data.transform.get_matrix()),
                  "frame_id":data.frame, "capture_sim_ns":round(data.timestamp*1e9)}
        with self.lock:
            self.buffers[label][data.frame] = packet
            for frame in sorted(self.buffers[label])[:-24]:
                del self.buffers[label][frame]

    def _receive_instance(self, label, name, data):
        packet = {"raw":bytes(data.raw_data),"frame_id":data.frame,
                  "capture_sim_ns":round(data.timestamp*1e9),"width":data.width,"height":data.height}
        with self.lock:
            buffer = self.instance_buffers[label][name]
            buffer[data.frame] = packet
            for frame in sorted(buffer)[:-8]:
                del buffer[frame]

    def freeze(self, frame, labels, timeout_s=10):
        deadline = time.monotonic()+timeout_s
        while time.monotonic()<deadline:
            with self.lock:
                if self.errors:
                    raise RuntimeError("; ".join(self.errors[-3:]))
                if (frame in self.snapshots and all(frame in self.buffers[n] for n in labels)
                        and all(frame in buffer for n in labels for buffer in self.instance_buffers[n].values())):
                    truth = self.snapshots[frame]
                    packets = {n:dict(self.buffers[n][frame],instance_cameras={
                        name:buffer[frame] for name,buffer in self.instance_buffers[n].items()}) for n in labels}
                    timestamps = [p["capture_sim_ns"] for p in packets.values()]
                    timestamps += [camera["capture_sim_ns"] for p in packets.values() for camera in p["instance_cameras"].values()]
                    if any(abs(stamp-truth["capture_sim_ns"])>1000 for stamp in timestamps):
                        raise ValueError("Evaluation sensor/snapshot timestamp mismatch.")
                    return truth,packets
            time.sleep(.02)
        raise TimeoutError(f"Missing exact-frame evaluation evidence: {frame}")

    def close(self):
        if self.callback_id is not None:
            self.world.remove_on_tick(self.callback_id)
            self.callback_id = None
        for sensor in reversed(self.sensors):
            try:
                sensor.stop(); sensor.destroy()
            except RuntimeError:
                pass
        self.sensors.clear()


def evaluate_round(folder, truth, packets, node_labels, frame_id, decoded_evidence=None,
                   visibility_evidence=None,rgb_evidence=None):
    folder = Path(folder)
    if truth["frame_id"] != frame_id or any(p["frame_id"]!=frame_id for p in packets.values()):
        raise ValueError("Cannot mix evaluation frames.")
    predictions = {}
    for node in node_labels:
        name = "ego_own.npz" if node=="ego" else f"ego_received_{node}.npz"
        with np.load(folder/name,allow_pickle=False) as data:
            predictions[node] = {k:data[k] for k in data.files}
    with np.load(folder/"ego_fused.npz",allow_pickle=False) as data:
        fused = {k:data[k] for k in data.files}
    evidence = ({node:decode_semantic(packets[label]) for node,label in node_labels.items()}
                if decoded_evidence is None else decoded_evidence)
    if visibility_evidence is None:
        visibility_evidence = {node:decode_instance_cameras(packets[label]["instance_cameras"],frame_id)
            for node,label in node_labels.items() if packets.get(label,{}).get("instance_cameras")}
    if rgb_evidence:
        for node,cameras in rgb_evidence.items():
            for name,packet in cameras.items():
                if packet["frame_id"]!=frame_id:
                    raise ValueError("RGB evidence frame mismatch.")
                pixels = np.frombuffer(packet["raw"],np.uint8).reshape(450,800,4)
                cv2.imwrite(str(folder/f"{node}_rgb_{name}.png"),pixels[:,:,:3])
    for node,cameras in visibility_evidence.items():
        names = sorted(cameras)
        np.savez_compressed(folder/f"{node}_eval_only_instance.npz",frame_id=frame_id,
            names=np.array(names),ids=np.stack([cameras[n]["ids"] for n in names]),
            tags=np.stack([cameras[n]["tags"] for n in names]))
    objects, masks = [], {}
    for target in truth["targets"]:
        target_id,cls = target["actor_id"],target["expected_class"]
        per_node,world_hits = {}, {}
        for node,(xyz,ids,tags) in evidence.items():
            selected = ids==target_id
            world_hits[node] = xyz[selected]
            mask = points_mask(xyz[selected],predictions[node]["pose"])
            per_node[node] = score_prediction(predictions[node],mask,cls)
            values,counts = np.unique(tags[selected],return_counts=True)
            per_node[node].update(lidar_points=int(selected.sum()),
                semantic_tags={str(int(k)):int(v) for k,v in zip(values,counts)})
        all_hits = np.concatenate(list(world_hits.values()))
        union = points_mask(all_hits,predictions["ego"]["pose"])
        in_roi,footprint,roi_basis = target_roi(target,predictions["ego"]["pose"],union)
        # Raw actor boxes can disagree with collision geometry. Exact actor-ID
        # return cells are authoritative for scoring AND the displayed GT.
        masks[target_id] = union
        union_score = score_prediction(fused,union,cls)
        own_on_union = score_prediction(predictions["ego"],union,cls)
        presence = {"ego":score_presence(predictions["ego"],union),"fused":score_presence(fused,union)}
        own_hits = per_node["ego"]["lidar_points"]
        peer_hits = sum(v["lidar_points"] for n,v in per_node.items() if n!="ego")
        candidate = bool(union.any() and own_hits==0 and peer_hits>0)
        cameras = {node:{name:int((camera["ids"]==target_id).sum()) for name,camera in data.items()}
                   for node,data in visibility_evidence.items()}
        projected = camera_projection_counts(folder,"ego",all_hits,predictions["ego"]["pose"],frame_id)
        camera_checked = set(cameras)==set(predictions) and all(len(c)==6 for c in cameras.values())
        ego_pixels = sum(cameras["ego"].values()) if camera_checked else None
        peer_pixels = sum(sum(c.values()) for n,c in cameras.items() if n!="ego") if camera_checked else None
        occluded = bool(candidate and camera_checked and ego_pixels==0 and peer_pixels>=3
                        and projected is not None and sum(projected.values())>=3)
        objects.append(dict(target,in_ego_roi=in_roi,ego_lidar_points=own_hits,
            peer_lidar_points=peer_hits,per_node=per_node,ego_on_union=own_on_union,fused=union_score,
            displayed_gt="exact_actor_id_semantic_return_cells",
            presence=presence,rgb_target_pixels=cameras,
            ego_projected_target_points_in_fov=projected,
            occlusion_verified=occluded,
            occlusion_recovered=bool(occluded and not presence["ego"]["diagnostic_match"] and presence["fused"]["diagnostic_match"]),
            roi_basis=roi_basis,
            bbox_cells=int(footprint.sum()) if footprint is not None else None,
            bbox_return_cell_overlap=int((union&footprint).sum()) if footprint is not None else None,
            bbox_is_not_scoring_ground_truth=True,
            visibility="outside_ego_roi" if not in_roi else "ego_lidar_hit" if own_hits else
                       "peer_only_lidar_hits" if peer_hits else "no_target_returns",
            peer_only_lidar_candidate=candidate,
            candidate_recovered=bool(candidate and presence["fused"]["diagnostic_match"])))
    gt_presence,gt_valid = observed_presence_ground_truth(evidence,predictions["ego"]["pose"])
    report = {"frame_id":frame_id, "capture_sim_ns":truth["capture_sim_ns"],
        "evaluation_only":True, "gt_fed_to_model_or_fusion":False, "metric2_pass":None,
        "primary_goal":"Obstacle presence/location and cooperative recovery; exact category is secondary",
        "presence_criterion":"diagnostic only: >=3 exact target-return cells, >=50% predicted as any non-ground obstacle class",
        "criterion":"secondary category diagnostic only: >=3 exact target-return cells, >=50% correct expected class",
        "tolerance":"Separate 3x3 diagnostic dilation: +/-0.4m per axis, not the strict match criterion",
        "visibility_limit":"No ego LiDAR hit does NOT prove RGB camera occlusion. Outside ROI is not a miss.",
        "expected_labels":"Project object category from blueprint, not a proof of training semantic-tag compatibility",
        "objects":objects,
        "in_roi_objects":sum(o["in_ego_roi"] for o in objects),
        "fused_diagnostic_matches":sum(o["in_ego_roi"] and o["fused"]["diagnostic_match"] for o in objects),
        "peer_only_candidates":sum(o["peer_only_lidar_candidate"] for o in objects),
        "recovered_candidates":sum(o["candidate_recovered"] for o in objects)}
    report.update(ego_presence_matches=sum(o["in_ego_roi"] and o["presence"]["ego"]["diagnostic_match"] for o in objects),
        fused_presence_matches=sum(o["in_ego_roi"] and o["presence"]["fused"]["diagnostic_match"] for o in objects),
        presence_added_objects=sum(o["presence"]["fused"]["diagnostic_match"] and not o["presence"]["ego"]["diagnostic_match"] for o in objects),
        occluded_objects=sum(o["occlusion_verified"] for o in objects),
        occluded_recovered=sum(o["occlusion_recovered"] for o in objects),
        scene_presence={"ego":score_scene_presence(predictions["ego"],gt_presence,gt_valid),
                        "fused":score_scene_presence(fused,gt_presence,gt_valid)},
        occlusion_criterion="Same-frame 6 RGB instance cameras: ego zero target pixels, peer >=3, projected target in ego FOV; ego LiDAR zero, peer LiDAR positive")
    report.update(evaluate_anchors(objects,masks,predictions["ego"],fused))
    report["primary_goal"] = "Situation-region obstacle presence and cooperative hidden-area recovery; individual objects are secondary diagnostics"
    report["ego_road_state"] = truth.get("ego_road_state")
    (folder/"evaluation.json").write_text(json.dumps(report,indent=2))
    (folder/"ground_truth.json").write_text(json.dumps(truth,indent=2))
    for node,(xyz,ids,tags) in evidence.items():
        np.savez_compressed(folder/f"{node}_eval_only_semantic.npz",world_xyz=xyz,actor_ids=ids,tags=tags)
    render_evaluation(folder,report,predictions["ego"],fused,masks)
    render_camera_projection(folder,report,predictions,evidence)
    render_presence_evaluation(folder,report,predictions,fused,masks,gt_presence,gt_valid)
    render_visibility_evaluation(folder,report,visibility_evidence)
    return report


def render_evaluation(folder, report, own, fused, masks):
    gt = np.full((200,200,3),28,np.uint8)
    for o in report["objects"]:
        gt[masks[o["actor_id"]]] = COLORS[o["expected_class"]]
    gt_image = np.ascontiguousarray(gt[::-1,::-1,::-1])
    images = [cv2.resize(gt_image,(600,600),interpolation=cv2.INTER_NEAREST),
              render_bev(own["probabilities"],own["valid"]),
              render_bev(fused["probabilities"],fused["valid"])]
    overview = []
    for title,img in zip(["CARLA GT target returns (eval only)","EGO PREDICTION","EGO FUSED PREDICTION"],images):
        header = np.full((80,600,3),20,np.uint8)
        cv2.putText(header,title,(12,29),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,255,255),1)
        cv2.putText(header,f"F{report['frame_id']} | x forward up, y left left",(12,59),cv2.FONT_HERSHEY_SIMPLEX,.5,(210,210,210),1)
        for o in report["objects"]:
            mask = masks[o["actor_id"]]
            if mask.any():
                idx = np.argwhere(mask).mean(0)
                u,v = int((199-idx[1])*3),int((199-idx[0])*3)
                cv2.putText(img,str(o["actor_id"]),(u+6,v),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
        overview.append(np.vstack([header,img]))
    cv2.imwrite(str(folder/"evaluation_overview.png"),np.hstack(overview))
    rows = []
    for o in report["objects"]:
        mask = masks[o["actor_id"]]
        if not mask.any():
            continue
        # Fixed 16m crop, identical ego-coordinate window for GT/own/fused.
        centre = np.argwhere(mask).mean(0).astype(int)
        x0,y0 = np.clip(centre-20,0,160)
        panels = []
        for i,(name,pred) in enumerate([("GT",None),("ego",own),("fused",fused)]):
            if pred is None:
                rgb = gt
            else:
                rgb = COLORS[pred["probabilities"].argmax(0)].copy()
                rgb[~pred["valid"].astype(bool)] = 28
            image = np.ascontiguousarray(rgb[x0:x0+40,y0:y0+40][::-1,::-1,::-1])
            image = cv2.resize(image,(400,400),interpolation=cv2.INTER_NEAREST)
            local_mask = mask[x0:x0+40,y0:y0+40][::-1,::-1].astype(np.uint8)
            contours,_ = cv2.findContours(cv2.resize(local_mask,(400,400),interpolation=cv2.INTER_NEAREST),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image,contours,-1,(255,255,255),1)
            header = np.full((90,400,3),20,np.uint8)
            score = None if i==0 else o["ego_on_union"] if i==1 else o["fused"]
            lines = [f"Actor {o['actor_id']} / {name} / F{report['frame_id']}",
                     f"Expected: {o['expected_name']}",
                     o["visibility"] if score is None else f"Correct: {score['correct_cells']}/{score['gt_return_cells']} cells; {score['dominant_prediction']}"]
            for j,line in enumerate(lines):
                cv2.putText(header,line,(9,24+26*j),cv2.FONT_HERSHEY_SIMPLEX,.43,(255,255,255),1)
            panels.append(np.vstack([header,image]))
        rows.append(np.hstack(panels))
    if rows:
        legend = np.full((65,1200,3),20,np.uint8)
        cv2.putText(legend,"Yellow=object | orange=car | cyan=road | blue=pedestrian | purple=sidewalk | dark=no current return",(12,25),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
        cv2.putText(legend,"White outline=exact GT target-return cells, NOT a predicted obstacle. Same 16m crop in all columns.",(12,51),cv2.FONT_HERSHEY_SIMPLEX,.48,(255,255,255),1)
        cv2.imwrite(str(folder/"evaluation_crops.png"),np.vstack([legend,*rows]))


def render_presence_evaluation(folder, report, predictions, fused, masks, gt, gt_valid):
    from bev_fusion import fuse_bevs
    empty = dict(predictions["ego"],valid=np.zeros((200,200),np.uint8))
    peers = fuse_bevs(empty,[p for n,p in predictions.items() if n!="ego"])
    truth_image = np.full((200,200,3),28,np.uint8)
    truth_image[(gt==GROUND)&gt_valid] = (185,185,185)
    truth_image[(gt==OBSTACLE)&gt_valid] = (0,180,255)
    images = [np.ascontiguousarray(truth_image[::-1,::-1]),
              render_presence(predictions["ego"],200),render_presence(peers,200),render_presence(fused,200)]
    names = ["SEMANTIC RETURN GT","EGO ONLY","VERIFIED PEERS ONLY","EGO FUSED"]
    rows = []
    for target in report["objects"]:
        mask = masks[target["actor_id"]]
        if not mask.any():
            continue
        centre = np.argwhere(mask).mean(0).astype(int)
        x0,y0 = np.clip(centre-20,0,160)
        panels = []
        for index,(name,full) in enumerate(zip(names,images)):
            raw = full[160-x0:200-x0,160-y0:200-y0]
            image = cv2.resize(raw,(400,400),interpolation=cv2.INTER_NEAREST)
            local = mask[x0:x0+40,y0:y0+40][::-1,::-1].astype(np.uint8)
            contours,_ = cv2.findContours(cv2.resize(local,(400,400),interpolation=cv2.INTER_NEAREST),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image,contours,-1,(255,255,255),1)
            header = np.full((100,400,3),20,np.uint8)
            score = None if index==0 else score_presence([None,predictions["ego"],peers,fused][index],mask)
            detail = (f"ego lidar={target['ego_lidar_points']}; peer={target['peer_lidar_points']}" if score is None else
                      f"Obstacle: {score['obstacle_cells']}/{score['gt_return_cells']} cells")
            for j,line in enumerate([name,f"Actor {target['actor_id']} | F{report['frame_id']}",detail,
                "RGB occlusion: verified" if target["occlusion_verified"] else "RGB occlusion: not established"]):
                cv2.putText(header,line,(9,21+23*j),cv2.FONT_HERSHEY_SIMPLEX,.44,(255,255,255),1)
            panels.append(np.vstack([header,image]))
        legend = np.full((60,1600,3),20,np.uint8)
        cv2.putText(legend,"Orange=obstacle of ANY category | light gray=ground | purple=uncertain | dark=unobserved, NOT free space",(10,23),cv2.FONT_HERSHEY_SIMPLEX,.58,(255,255,255),1)
        cv2.putText(legend,"White outline=exact target-return GT; same 16m crop. GT never enters model, communication or fusion.",(10,48),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
        row = np.hstack(panels)
        cv2.imwrite(str(folder/f"presence_target_{target['actor_id']}.png"),np.vstack([legend,row]))
        for node,prediction in (("ego",predictions["ego"]),("fused",fused)):
            comparison,_ = render_presence_comparison(prediction,
                title=f"{node.upper()} | Actor {target['actor_id']}",frame_id=report["frame_id"],
                crop=(int(x0),int(y0),40),target_mask=mask)
            cv2.imwrite(str(folder/f"presence_interpolation_{node}_target_{target['actor_id']}.png"),comparison)
        rows.append(row)
    if rows:
        cv2.imwrite(str(folder/"presence_crops.png"),np.vstack([legend,*rows]))


def render_visibility_evaluation(folder, report, evidence):
    for target in report["objects"]:
        counts = target["rgb_target_pixels"]
        if not counts:
            continue
        projected = target["ego_projected_target_points_in_fov"] or counts["ego"]
        ego_camera = max(projected,key=projected.get)
        choices = [(value,node,name) for node,cameras in counts.items() if node!="ego" for name,value in cameras.items()]
        if not choices:
            continue
        _,peer,peer_camera = max(choices)
        panels = []
        for node,name in [("ego",ego_camera),(peer,peer_camera)]:
            image = cv2.imread(str(folder/f"{node}_rgb_{name}.png"))
            if image is None:
                break
            mask = (evidence[node][name]["ids"]==target["actor_id"]).astype(np.uint8)
            contours,_ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image,contours,-1,(0,255,255),2)
            header = np.full((80,800,3),20,np.uint8)
            lines = [f"{node} {name} | actor {target['actor_id']} | F{report['frame_id']}",
                     f"Target visible pixels: {counts[node][name]} | yellow outline = evaluation GT only"]
            for j,line in enumerate(lines):
                cv2.putText(header,line,(10,29+32*j),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
            panels.append(np.vstack([header,image]))
        if len(panels)==2:
            cv2.imwrite(str(folder/f"visibility_target_{target['actor_id']}.png"),np.hstack(panels))


def render_camera_projection(folder, report, predictions, evidence):
    from bev_sensor_rig import CAMERAS,MEAN,STD
    targets = {o["actor_id"] for o in report["objects"]}
    for node,pred in predictions.items():
        path = folder/f"{node}_model_inputs.npz"
        if not path.exists():
            continue
        with np.load(path,allow_pickle=False) as data:
            if int(data["frame_ids"][-1])!=report["frame_id"]:
                raise ValueError("Projection image frame mismatch.")
            xyz,ids,_ = evidence[node]
            keep = np.isin(ids,list(targets))
            points = local_points(xyz[keep],pred["pose"])
            tiles = []
            for k,name in enumerate(CAMERAS):
                rgb = np.clip((data["imgs"][0,-1,k].transpose(1,2,0)*STD+MEAN)*255,0,255).round().astype(np.uint8)
                image = cv2.resize(rgb[:,:,::-1],(640,256))
                optical = (points-data["trans"][0,-1,k]) @ data["rots"][0,-1,k]
                q = optical @ data["intrins"][0,-1,k].T
                uv = q / np.maximum(q[:,2:3],1e-8)
                uv = uv @ data["post_rots"][0,-1,k].T + data["post_trans"][0,-1,k]
                ok = (optical[:,2]>.1)&(uv[:,0]>=0)&(uv[:,0]<320)&(uv[:,1]>=0)&(uv[:,1]<128)
                for u,v in uv[ok,:2]:
                    cv2.circle(image,(round(float(u)*2),round(float(v)*2)),2,(0,255,0),-1)
                header = np.full((30,640,3),20,np.uint8)
                text = f"{node} {name} F{report['frame_id']} | GT lidar projection {int(ok.sum())} pts"
                cv2.putText(header,text,(8,21),cv2.FONT_HERSHEY_SIMPLEX,.46,(255,255,255),1)
                tiles.append(np.vstack([header,image]))
            cv2.imwrite(str(folder/f"{node}_projection_check.png"),np.vstack([np.hstack(tiles[:3]),np.hstack(tiles[3:])]))
