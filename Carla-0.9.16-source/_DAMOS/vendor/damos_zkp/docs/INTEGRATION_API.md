# Integration API

This handoff begins **after fusion**. The security module does not run CARLA, LiDAR callbacks, object association, or fusion.

## Sender

```python
from sdk import DAMOSSecuritySender

sender = DAMOSSecuritySender(
    anchor_id="main-computer",
    recipient_ids=["vehicle", "delivery-robot", "humanoid"],
    anchor_secret=load_secret_securely(),
)

payload = sender.make_payload(
    objects=fused_objects,
    source_ids=["vehicle_lidar", "delivery_robot_lidar", "humanoid_lidar"],
    fusion_id=fusion_id,
    anchor_timestamp_ms=fusion_timestamp_ms,
    coordinate_frame="world",
)
wire = sender.protect(payload)
```

The **same `wire` bytes** can be sent to every recipient committed in `recipient_ids`. One Groth16 proof is generated for the whole recipient set.

## Receiver

```python
from sdk import DAMOSSecurityReceiver

receiver = DAMOSSecurityReceiver(
    self_id="vehicle",
    anchor_id="main-computer",
    registered_commitments=[pre_registered_anchor_commitment],
)

payload, report = receiver.verify(received_wire)
if report.accepted:
    consume_verified_fusion(payload)
else:
    drop_and_log(report.reject_reason)
```

The receiver must obtain `registered_commitment` through a trusted enrollment/configuration path. Never trust a commitment merely because it appears inside the received packet.

## Fused object schema

Every object must contain exactly these integer fields:

```text
object_id
semantic_tag
center_x_mm
center_y_mm
center_z_mm
length_mm
width_mm
height_mm
min_distance_mm
point_count
```

`object_id` must be unique within the fused list. The security module sorts by `object_id`. If the fusion pipeline keeps per-sensor local IDs, remap them to unique fused IDs before calling `protect()`.

## Security boundary

The module verifies that a holder of the registered anchor secret authorized the exact committed payload/context and that the object list was not modified after commitment. It also enforces recipient membership, timestamp window, epoch/sequence replay protection, manifest consistency, Merkle recomputation, public-input consistency, and Groth16 verification.

It does **not** prove that LiDAR measurements are physically true, that fusion is correct, that no object was omitted before commitment, or that the anchor host itself is uncompromised.
