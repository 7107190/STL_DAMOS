"""Bounded, preselected temporal holdout subset from the supplied NAS dataset."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image


def read_json(path):
    with path.open() as stream:
        return json.load(stream)


def dump_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2))


def contained(root, name):
    path = (root/name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Unsafe dataset relative path.")
    return path


def prepare(args, session, agent):
    uid = session+"__"+agent
    source = args.source/session/"nuscenes"/agent
    metadata = source/"v1.0-mini"
    destination = args.output/"images"/uid
    tables = {}
    for name in ("sample","sample_data","calibrated_sensor","ego_pose","scene",
                 "sensor","category","instance","attribute","visibility","log","map"):
        tables[name] = read_json(metadata/(name+".json"))
    by_token = {name:{r["token"]:r for r in rows} for name,rows in tables.items()}
    timestamps = {r["timestamp"]:r["token"] for r in tables["sample"]}
    records = defaultdict(dict)
    for row in tables["sample_data"]:
        channel = Path(row["filename"]).parent.name
        records[row["timestamp"]][channel] = row
        # Same token repair used by prep_images.py.
        if row["sample_token"] not in by_token["sample"] and row["timestamp"] in timestamps:
            row["sample_token"] = timestamps[row["timestamp"]]
    windows = []
    for scene in tables["scene"]:
        sequence = []
        token = scene["first_sample_token"]
        while token:
            row = by_token["sample"][token]
            sequence.append(row)
            token = row["next"]
        # Original GT generator defaults to six prior/current keyframes;
        # inference uses the last four. Missing original GT files prevent an
        # exact reconstruction of its full validation index.
        eligible = [sequence[i-3:i+1] for i in range(5,len(sequence))
                    if "LIDAR_SEGMENTATION" in records[sequence[i]["timestamp"]]]
        cut = int(round(len(eligible)*.9))
        holdout = eligible[cut:]
        picks = np.linspace(0,len(holdout)-1,min(args.per_agent,len(holdout))).astype(int)
        windows.extend(holdout[i] for i in picks)
    if not windows:
        raise ValueError("No eligible held-out windows for "+uid)
    targets = {window[-1]["token"] for window in windows}
    print(f"{uid}: {len(windows)} preselected windows; reading annotation table",flush=True)
    annotations = [r for r in read_json(metadata/"sample_annotation.json") if r["sample_token"] in targets]
    annotation_by_sample = defaultdict(list)
    for row in annotations:
        annotation_by_sample[row["sample_token"]].append(row)

    core_dir = args.code/"carla" if args.code else Path(__file__).resolve().parents[1]/"vendor"/"yerin_bev"
    sys.path.insert(0,str(core_dir))
    import carla_gt_core as G
    gt_dir = args.output/"gt"/uid/"npz"
    gt_dir.mkdir(parents=True,exist_ok=True)
    output = []
    copied = set()
    channels = ["CAM_FRONT_LEFT","CAM_FRONT","CAM_FRONT_RIGHT",
                "CAM_BACK_LEFT","CAM_BACK","CAM_BACK_RIGHT"]
    for index,window in enumerate(windows):
        for sample in window:
            for channel in channels+["LIDAR_TOP"]:
                record = records[sample["timestamp"]][channel]
                name = record["filename"]
                if name in copied:
                    continue
                destination_file = contained(destination,name)
                destination_file.parent.mkdir(parents=True,exist_ok=True)
                source_file = contained(source,name)
                if channel.startswith("CAM_"):
                    with Image.open(source_file) as image:
                        image = image.convert("RGB")
                        image.resize((round(image.width*.5),round(image.height*.5)),
                                     Image.Resampling.BILINEAR).save(destination_file,"JPEG",quality=92)
                else:
                    shutil.copyfile(source_file,destination_file)
                copied.add(name)
        current = window[-1]
        seg = records[current["timestamp"]]["LIDAR_SEGMENTATION"]
        cs = by_token["calibrated_sensor"][seg["calibrated_sensor_token"]]
        ep = by_token["ego_pose"][seg["ego_pose_token"]]
        raw = contained(source,seg["filename"]).read_bytes()
        if len(raw)%24:
            raise ValueError("Invalid original semantic point bytes.")
        points = np.frombuffer(raw,dtype=np.float32).reshape(-1,6)
        xyz = points[:,:3].astype(np.float64)@G.quat2rot(cs["rotation"]).T+cs["translation"]
        xyz[:,1] *= -1
        rotation = G.quat2rot(ep["rotation"])
        boxes = []
        for annotation in annotation_by_sample[current["token"]]:
            instance = by_token["instance"][annotation["instance_token"]]
            category = by_token["category"][instance["category_token"]]["name"]
            centre = (np.array(annotation["translation"])-ep["translation"])@rotation
            width,length,height = annotation["size"]
            boxes.append((G.box_cat2cls(category),*centre,width,length,height,
                          G.yaw_of(annotation["rotation"])-G.yaw_of(ep["rotation"])))
        sem,observed = G.build_voxel_semantics(xyz,points[:,5].astype(int),boxes)
        # OpenCV is available on vvu; apply the unchanged author's densify
        # there before evaluation rather than adding a local dependency.
        bev,mask = G.collapse_bev(sem,observed)
        np.savez_compressed(gt_dir/(current["token"]+".npz"),
                            bev_cls_future=bev[None],bev_mask_future=mask[None],
                            motion_patch=np.zeros((0,G.NZ,25,25),np.float32),
                            densify_pending=np.array(True))
        cameras = {}
        for channel in channels:
            rec = records[current["timestamp"]][channel]
            cal = by_token["calibrated_sensor"][rec["calibrated_sensor_token"]]
            width = rec["width"]
            focal = cal["camera_intrinsic"][0][0]
            cameras[channel] = {"fov_degrees":float(np.degrees(2*np.arctan(width/(2*focal)))),
                "raw_width":width,"raw_height":rec["height"],"translation":cal["translation"],
                "rotation":cal["rotation"],"localized_intrinsic":(np.array(cal["camera_intrinsic"])*np.array([[.5],[.5],[1.]])).tolist()}
        output.append({"uid":uid,"token":current["token"],
            "past_tokens":[r["token"] for r in window],
            "timestamps_us":[r["timestamp"] for r in window],
            "dt_seconds":(np.diff([r["timestamp"] for r in window])/1e6).tolist(),
            "cameras":cameras,"semantic_sha256":hashlib.sha256(raw).hexdigest(),
            "gt_source":"original carla_gt_core; semantic points + original annotations, densify on"})
        print(f"{uid}: sample {index+1}/{len(windows)}, {len(boxes)} boxes",flush=True)
    modalities = {r["token"]:r["modality"] for r in tables["sensor"]}
    for calibration in tables["calibrated_sensor"]:
        if modalities[calibration["sensor_token"]]=="camera":
            calibration["camera_intrinsic"] = (np.array(calibration["camera_intrinsic"])*np.array([[.5],[.5],[1.]])).tolist()
    for record in tables["sample_data"]:
        if Path(record["filename"]).parent.name.startswith("CAM_"):
            record["width"] = round(record["width"]*.5)
            record["height"] = round(record["height"]*.5)
    tables["sample_annotation"],tables["instance"] = [],[]
    for name,rows in tables.items():
        dump_json(destination/"v1.0-mini"/(name+".json"),rows)
    for row in tables["map"]:
        source_file = contained(source,row["filename"])
        destination_file = contained(destination,row["filename"])
        destination_file.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source_file,destination_file)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--code",type=Path,help="Original handoff code directory; default uses vendored GT core")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--per-agent",type=int,default=6)
    args = parser.parse_args()
    samples = []
    for session,agent in [("20260304_102529_Clear_Noon_3_vehicles","agent_1635"),
                          ("20260224_163245_Clear_Night","agent_024")]:
        samples.extend(prepare(args,session,agent))
    dump_json(args.output/"selection.json",{
        "selection":"Evenly spaced last 10% temporal windows; chosen before inference",
        "limit":"12-window diagnostic, not reproduction of the reported full 2123-frame metric; original GT cache/index unavailable",
        "samples":samples})


if __name__=="__main__":
    main()
