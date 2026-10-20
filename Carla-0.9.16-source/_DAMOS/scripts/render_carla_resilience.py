"""Compact scientific figures from actual CARLA fault-response records."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np


def render(root, output):
    output.mkdir(parents=True, exist_ok=False)
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
    fig, ax = plt.subplots(figsize=(10, 5.8), layout='constrained')
    ax.imshow(matrix, cmap=ListedColormap(['#dedede', '#cae6d3', '#ffe5ad', '#f6b7b7']),
              vmin=0, vmax=3, aspect='auto')
    ax.set_xticks(range(len(weather)), weather)
    ax.set_yticks(range(len(names)), [f'C{i+1:02d}  {n.replace("_", " ")}' for i, n in enumerate(names)])
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
    fig.supxlabel('Three S5 scenes; seven rounds per condition. Availability/authenticity, not fault-state detection accuracy.', fontsize=9)
    fig.savefig(output / 'service_response_matrix.png', dpi=180)
    plt.close(fig)

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
