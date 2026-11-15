"""Compact scientific figures from actual CARLA fault-response records."""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np

from fault_catalog import resolve_fault


def render_quality_faults(root, output, cells, weather):
    import cv2
    from bev_fault_quality import QUALITY_FAULTS, load_raw_task
    from bev_occupancy import render_presence
    from bev_sensor_rig import HEIGHT, WIDTH
    names = [name for name in QUALITY_FAULTS if (name, weather) in cells]
    if not names:
        return
    fig, axes = plt.subplots(4, len(names), figsize=(4.4 * len(names), 13), squeeze=False, layout='constrained')
    for col, name in enumerate(names):
        case = cells[name, weather]
        row = next(r for r in case['rounds'] if r['phase'] == 'fault')
        folder = root / weather / 'resilience' / row['path'] / 'ego'
        before = load_raw_task(folder / ('ego_quality_reference.npz' if name == 'camera_misalignment' else 'ego_raw_before.npz'))
        applied = load_raw_task(folder / 'ego_raw_applied.npz')
        for index, task in enumerate((before, applied)):
            ax = axes[index, col]
            if name.startswith('camera_'):
                packet = task['packets'][task['label'], 'cam_front'][-1]
                rgb = np.frombuffer(packet['raw'], np.uint8).reshape(HEIGHT, WIDTH, 4)[:, :, :3][:, :, ::-1]
                ax.imshow(rgb)
                ax.axis('off')
            else:
                packet = task['packets'][task['label'], 'lidar'][-1]
                points = np.frombuffer(packet['raw'], np.float32).reshape(-1, 4)
                ax.set_facecolor('#111519')
                ax.scatter(points[:, 0], -points[:, 1], s=1.6, color='#f59a32', linewidths=0, rasterized=True)
                ax.set_xlim(-40, 40); ax.set_ylim(-40, 40); ax.set_aspect('equal')
                ax.tick_params(labelsize=8)
            source = 'REFERENCE' if index == 0 else 'APPLIED FAULT'
            ax.set_title(f'{source} F{task["window"][-1]}', fontsize=10)
        for index, filename in ((2, 'ego_rejected_candidate.npz'), (3, 'ego_fused.npz')):
            with np.load(folder / filename, allow_pickle=False) as archive:
                bev = {key: archive[key] for key in archive.files}
            image = cv2.cvtColor(render_presence(bev), cv2.COLOR_BGR2RGB)
            axes[index, col].imshow(image)
            axes[index, col].axis('off')
            axes[index, col].set_title('Actual model output - REJECTED' if index == 2 else row['decision']['state'], fontsize=10)
        fault = resolve_fault(name)
        axes[0, col].set_title(f'{fault.fault_id} {fault.label}\n{axes[0, col].get_title()}', fontsize=11)
    fig.suptitle(f'Actual input corruption, CUDA candidate rejection and authenticated peer fallback | {weather}', fontsize=16)
    fig.supxlabel('Rows: original/reference raw input; applied raw fault; rejected CUDA output; ego fused output. '
                  'F03 reference is the prior normal frame; physical sensor yaw changed by 25 degrees. '
                  'Availability response, not target-detection accuracy.', fontsize=10)
    fig.savefig(output / 'quality_faults_overview.png', dpi=150)
    plt.close(fig)


def render(root, output):
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).read_bytes()
    (output / 'renderer.py').write_bytes(source)
    (output / 'rendering_provenance.json').write_text(json.dumps({
        'renderer_sha256': hashlib.sha256(source).hexdigest(),
        'summary_sha256': hashlib.sha256((root / 'summary.json').read_bytes()).hexdigest(),
        'protocol_sha256': hashlib.sha256((root / 'protocol.json').read_bytes()).hexdigest(),
        'scope': 'post-processing saved trial records; no new scene or model execution'}, indent=2))
    summary = json.loads((root / 'summary.json').read_text())
    protocol = json.loads((root / 'protocol.json').read_text())
    names, weather = protocol['cases'], protocol['weathers']
    matrix = np.zeros((len(names), len(weather)), dtype=int)
    cells = {}
    for execution in summary['executions']:
        for case in execution['summary']['cases']:
            cells[case['case'], case['weather']] = case
    for i, name in enumerate(names):
        for j, condition in enumerate(weather):
            case = cells.get((name, condition))
            matrix[i, j] = 0 if case is None else 3 if not case['criterion_pass'] else (
                1 if case['expected_fault_state'] == 'DEGRADED_COOPERATIVE' else 2)
    fig, ax = plt.subplots(figsize=(12, max(5.8, len(names) * .60)), layout='constrained')
    ax.imshow(matrix, cmap=ListedColormap(['#dedede', '#cae6d3', '#ffe5ad', '#f6b7b7']),
              vmin=0, vmax=3, aspect='auto')
    ax.set_xticks(range(len(weather)), weather)
    ax.set_yticks(range(len(names)), [f'{resolve_fault(n).fault_id}  {n.replace("_", " ")}' for n in names])
    for i in range(len(names)):
        for j in range(len(weather)):
            value = matrix[i, j]
            text = ('NOT RUN', 'PEER FALLBACK\nPASS', 'BRAKE CHECK\nSERVICE RECOVERY\nPASS', 'FAIL')[value]
            ax.text(j, i, text, ha='center', va='center', fontsize=9, color='#17201b')
    ax.set_xticks(np.arange(-.5, len(weather), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(names), 1), minor=True)
    ax.grid(which='minor', color='white', linewidth=2)
    ax.tick_params(which='minor', bottom=False, left=False)
    ax.set_title(f'CARLA monitoring response: {summary["successful"]}/{summary["planned"]} conditions passed', fontsize=15)
    fig.supxlabel(f'{len(weather)} S5 scene(s); seven rounds per condition. Availability/authenticity, not fault-state detection accuracy.', fontsize=9)
    fig.savefig(output / 'service_response_matrix.png', dpi=180)
    plt.close(fig)
    render_quality_faults(root, output, cells, weather[0])

    case = cells.get(('front_blackout', weather[0]))
    if case is None:
        return
    chosen = [next(r for r in case['rounds'] if r['phase'] == 'baseline' and r['phase_index'] == 3),
              next(r for r in case['rounds'] if r['phase'] == 'fault'),
              next(r for r in case['rounds'] if r['phase'] == 'recovery' and r['phase_index'] == 3)]
    source = root / weather[0] / 'resilience'
    observer = next(n for n in chosen[0]['peers'] if n != 'ego')
    fig, axes = plt.subplots(3, 3, figsize=(15, 11), layout='constrained')
    for j, row in enumerate(chosen):
        path = source / row['path']
        paths = [path / 'ego/ego_input_front.png', path / f'{observer}_current_front.png',
                 path / 'ego/ego_presence.png']
        for i, image in enumerate(paths):
            axes[i, j].imshow(plt.imread(image))
            axes[i, j].axis('off')
        axes[0, j].set_title(f'{row["phase"].upper()} | Ego input F{row["ego"]["health"]["lidar_frame_id"]}\n{row["decision"]["state"]}', fontsize=12)
        axes[1, j].set_title(f'Observer RGB F{row["frame_id"]} | verified peers={len(row["ego"]["verified_peer_ids"])}', fontsize=11)
        axes[2, j].set_title(f'Ego fused BEV | local usable={row["ego"]["local_usable"]}', fontsize=11)
        axes[2, j].scatter([300], [300], color='red', s=35, marker='^')
        axes[2, j].text(311, 305, 'EGO', color='white', fontsize=9)
    fig.suptitle('Actual front-camera signal loss, authenticated observer fallback and service recovery', fontsize=15)
    fig.supxlabel('Different consecutive capture times; same actor IDs. Orange: obstacle prediction. Gray: ground. Dark: unobserved.', fontsize=10)
    fig.savefig(output / 'front_blackout_sequence.png', dpi=140)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    render(args.root, args.output)
