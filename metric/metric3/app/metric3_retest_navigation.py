"""Repair junction-start route omissions without changing the locked scene."""
import sys
from pathlib import Path

from damos_paths import runtime_root

ROOT = runtime_root(Path(__file__).resolve().parent.parent) / 'Carla-0.9.16-source'


def configure_route(rig, client, assignment, scenario, tm_port):
    sys.path.insert(0, str(ROOT / 'PythonAPI/carla'))
    import carla
    from agents.navigation.local_planner import LocalPlanner, RoadOption
    from agents.navigation.global_route_planner import GlobalRoutePlanner
    from bev_passby_video import video_waypoint, FPS

    road = rig.world.get_map()
    ego = rig.actors['ego']
    anchor = assignment['anchor_location']
    goal = video_waypoint(road, anchor['x'], anchor['y'], scenario)
    planner = GlobalRoutePlanner(road, 2.)
    planner._graph.remove_edges_from([(a, b) for a, b, data in planner._graph.edges(data=True)
        if data['type'] in (RoadOption.CHANGELANELEFT, RoadOption.CHANGELANERIGHT)])
    prefix = [road.get_waypoint(ego.get_location(), True, carla.LaneType.Driving)]
    length = 0.
    # Junction graph localization omitted the actual starting connector. Follow
    # its legal forward waypoints before joining the existing global planner.
    while length < 20. or prefix[-1].is_junction:
        current = prefix[-1]
        candidates = current.next(2.)
        if not candidates or length > 80.:
            raise RuntimeError('No legal exit from the locked Ego start')
        nxt = min(candidates, key=lambda p: (
            abs((p.transform.rotation.yaw-current.transform.rotation.yaw+180)%360-180),
            p.road_id, p.lane_id, p.s))
        length += nxt.transform.location.distance(current.transform.location)
        prefix.append(nxt)
    tail = planner.trace_route(prefix[-1].transform.location, goal.transform.location)
    after = min(goal.next(30.), key=lambda p: abs(
        (p.transform.rotation.yaw-goal.transform.rotation.yaw+180)%360-180))
    route = [(w, RoadOption.LANEFOLLOW) for w in prefix] + tail
    route += planner.trace_route(goal.transform.location, after.transform.location)
    gaps = [a[0].transform.location.distance(b[0].transform.location)
            for a, b in zip(route, route[1:])]
    if not gaps or max(gaps) > 4.5 or route[0][0].transform.location.distance(ego.get_location()) > 1.:
        raise RuntimeError('Discontinuous or mislocalized approach route')
    speed = 8. if scenario in ('S1', 'S2', 'S3') else 15.
    dt = 1. / FPS
    ego.set_autopilot(False, tm_port)
    agent = LocalPlanner(ego, map_inst=road, opt_dict={
        'dt': dt, 'target_speed': speed, 'sampling_radius': 2.,
        'lateral_control_dict': {'K_P': 1.95, 'K_I': .05, 'K_D': .2, 'dt': dt},
        'longitudinal_control_dict': {'K_P': 1., 'K_I': .05, 'K_D': 0., 'dt': dt}})
    agent.set_global_plan(route)
    return agent, {
        'desired_speed_kmh': speed, 'traffic_manager_port_disabled': tm_port,
        'controller': 'CARLA LocalPlanner/PID; original speed and PID gains',
        'control_period_seconds': dt,
        'planner': 'CARLA GlobalRoutePlanner with legal initial-junction waypoint prefix and no lane-change shortcuts',
        'initial_connector_prefix_m': length, 'maximum_waypoint_gap_m': max(gaps),
        'teleport': False, 'pedestrian_avoidance': False,
        'collision_rejection': True, 'sensor_readiness_brake_retained': True,
        'operational_retest_navigation_fix': True,
        'path_carla': [[w.transform.location.x, w.transform.location.y] for w, _ in route],
    }
