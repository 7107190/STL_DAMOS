"""Real Groth16 end-to-end example. No CARLA required."""
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sdk import DAMOSSecuritySender, DAMOSSecurityReceiver
from anchor.broadcast_schema import normalize_broadcast_payload
from anchor.broadcast_serializer import BroadcastSerializer
from app.config import SNARK_FIELD_MODULUS

ANCHOR_ID = 'main-computer'
RECIPIENTS = ['vehicle', 'delivery-robot', 'humanoid']
SOURCE_IDS = ['vehicle_lidar', 'delivery_robot_lidar', 'humanoid_lidar']

OBJECTS = [
    {
        'object_id': 1,
        'semantic_tag': 14,
        'center_x_mm': 4200,
        'center_y_mm': -350,
        'center_z_mm': 100,
        'length_mm': 3800,
        'width_mm': 1750,
        'height_mm': 1500,
        'min_distance_mm': 4010,
        'point_count': 124,
    },
    {
        'object_id': 2,
        'semantic_tag': 12,
        'center_x_mm': 7100,
        'center_y_mm': 860,
        'center_z_mm': 900,
        'length_mm': 650,
        'width_mm': 520,
        'height_mm': 1760,
        'min_distance_mm': 6810,
        'point_count': 51,
    },
]

# For deployment, store this outside source control and provision it securely.
secret = os.environ.get('DAMOS_ANCHOR_SECRET')
if secret is None:
    secret = str(secrets.randbelow(SNARK_FIELD_MODULUS - 1) + 1)
    print('DAMOS_ANCHOR_SECRET not set; using an ephemeral self-test secret (not printed).')

sender = DAMOSSecuritySender(ANCHOR_ID, RECIPIENTS, secret)
print('registered_commitment =', sender.registered_commitment)

payload = sender.make_payload(OBJECTS, SOURCE_IDS, fusion_id=1, coordinate_frame='world')
wire = sender.protect(payload)
print('wire_bytes =', len(wire))
print('proof_timing =', BroadcastSerializer.from_bytes(wire).zkp_timing)

for recipient in RECIPIENTS:
    receiver = DAMOSSecurityReceiver(
        recipient, ANCHOR_ID, [sender.registered_commitment])
    accepted_payload, report = receiver.verify(wire)
    print(recipient, report.summary_line(), report.timing)
    assert report.accepted
    assert accepted_payload == normalize_broadcast_payload(payload)
