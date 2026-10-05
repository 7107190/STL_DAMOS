#!/usr/bin/env python3
"""Real proof/TCP smoke test with explicit synthetic inputs (not CARLA evidence)."""
import argparse
import json
from pathlib import Path

import numpy as np

from m2x.live import write_summary
from m2x.mesh import PeerMesh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--nodes", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1 or not 2 <= args.nodes <= 8:
        parser.error("rounds>=1, 2<=nodes<=8 required")
    args.output.mkdir(parents=True, exist_ok=False)
    ids = ["ego"] + [f"observer_{n}" for n in range(1, args.nodes)]
    mesh, records = PeerMesh(ids), []
    try:
        for round_id in range(1, args.rounds + 1):
            samples = {}
            for i, node in enumerate(ids):
                image = np.zeros((240, 320, 4), dtype=np.uint8)
                image[:, :, i % 3] = 80 + 20 * round_id
                image[:, :, 3] = 255
                samples[node] = {"raw": image.tobytes(), "actor_id": i + 1,
                                 "frame_id": round_id, "capture_sim_ns": round_id * 100000000,
                                 "width": 320, "height": 240, "position": [i * 5, 0, 0]}
            rows = mesh.exchange(samples, round_id, save_dir=args.output / f"round_{round_id:03d}")
            for node, row in rows.items():
                print(f"{node}: {row['status']} {row['elapsed_ms']:.1f}ms {row.get('error', '')}", flush=True)
            records.append(rows["ego"])
            with (args.output / "peer_debug.jsonl").open("a") as output:
                for row in rows.values():
                    output.write(json.dumps(row) + "\n")
        summary = write_summary(args.output, records, source="synthetic_smoke_only")
        if summary["sharing_successes"] != args.rounds:
            raise SystemExit(1)
    finally:
        mesh.close()


if __name__ == "__main__":
    main()
