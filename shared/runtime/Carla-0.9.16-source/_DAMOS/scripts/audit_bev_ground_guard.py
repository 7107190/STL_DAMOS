"""Verify original evidence hashes and corrected video timeline/pixel integrity."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decoded_hashes(path):
    output = subprocess.check_output([shutil.which("ffmpeg"), "-v", "error", "-i", str(path),
        "-an", "-f", "framemd5", "pipe:1"], text=True)
    lines = (line for line in output.splitlines() if not line.startswith("#"))
    return [row[-1].strip() for row in csv.reader(lines) if row]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--videos", type=Path, required=True)
    args = parser.parse_args()
    index = json.loads(args.index.read_text())
    verified, failures, decoded = 0, [], []
    for row in index["rows"]:
        root = Path(row["source_archive"])
        for relative, expected in json.loads((root / "sha256_manifest.json").read_text()).items():
            if sha(root / relative) != expected:
                failures.append(str(root / relative))
            verified += 1
        print(row["scenario"] + " original evidence verified", flush=True)
        path = args.videos / f"{row['scenario']}_ground_guard_2x3.mp4"
        probe = json.loads(subprocess.check_output([shutil.which("ffprobe"), "-v", "error", "-count_frames",
            "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames", "-show_entries", "format=duration",
            "-of", "json", str(path)]))
        stream = probe["streams"][0]
        if ((stream["width"], stream["height"], stream["r_frame_rate"], int(stream["nb_read_frames"]))
                != (1964, 1320, "5/1", row["frames"])):
            raise AssertionError("Corrected video dimensions/fps/frame count changed.")
        frames = decoded_hashes(path)
        if len(frames) != row["frames"]:
            raise AssertionError("Corrected decoded timeline length changed.")
        decoded.extend(frames)
    merged = args.videos / "S1-S9_ground_guard_2x3.mp4"
    identical = decoded_hashes(merged) == decoded
    report = {"original_files_checked": verified, "original_hash_failures": failures,
        "original_evidence_unchanged": not failures, "decoded_frames": len(decoded),
        "merged_matches_individual_decoded_frames": identical,
        "duration_seconds": len(decoded) / 5, "all_passed": not failures and identical}
    (args.videos / "integrity_audit.json").write_text(json.dumps(report, indent=2))
    if not report["all_passed"]:
        raise AssertionError("Corrected video integrity failed.")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
