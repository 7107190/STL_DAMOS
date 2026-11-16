"""Keep the existing agent active after acquisition while queued inference finishes."""
import time


def maintain(world, ego, agent, stop, records, errors):
    try:
        next_frame=world.get_snapshot().frame
        while not stop.wait(.02):
            frame=world.get_snapshot().frame
            if frame<next_frame:
                continue
            control=agent.run_step()
            if control is not None:
                ego.apply_control(control)
            records.append({"frame_id":frame,"wall_ns":time.time_ns(),"control_applied":control is not None})
            next_frame=frame+2
    except Exception as exc:
        errors.append(type(exc).__name__+": "+str(exc))

