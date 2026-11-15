"""Observed-cell occupancy calibration from ordinary LiDAR and model probabilities only."""
import numpy as np

from bev_occupancy import OBSTACLE_CLASSES, GROUND_CLASSES

VERSION = "ordinary-lidar-occupancy-v1"
FEATURES = ("model_margin","max_obstacle_probability","max_ground_probability","entropy",
            "height_above_plane_max","height_above_plane_min","vertical_span","log_count","range")


def geometry_features(lidar):
    from sklearn.linear_model import RANSACRegressor
    lidar = np.asarray(lidar,np.float32)
    if lidar.shape==(1,6,200,200):
        lidar = lidar[0]
    if lidar.shape!=(6,200,200) or not np.isfinite(lidar).all():
        raise ValueError("Invalid ordinary LiDAR raster.")
    valid = lidar[0]>0
    axis = -40+(np.arange(200)+.5)*.4
    x,y = np.meshgrid(axis,axis,indexing="ij")
    radius = np.hypot(x,y)
    low = lidar[3]
    flat = valid&(radius>2)&(radius<35)&(lidar[4]<.15)
    if flat.sum()<32:
        flat = valid
    selected = np.flatnonzero(flat)
    if len(selected):
        cutoff = np.quantile(low.ravel()[selected],.35)+.12
        selected = selected[low.ravel()[selected]<=cutoff]
    floor = float(np.median(low.ravel()[selected])) if len(selected) else 0.
    plane = np.full((200,200),floor,np.float64)
    if len(selected)>=32:
        selected = selected[np.linspace(0,len(selected)-1,min(2048,len(selected))).astype(int)]
        xy = np.column_stack([x.ravel()[selected],y.ravel()[selected]])
        fit = RANSACRegressor(random_state=7190,min_samples=3,residual_threshold=.12,max_trials=32)
        try:
            fit.fit(xy,low.ravel()[selected])
            slope = fit.estimator_.coef_
            if np.isfinite(slope).all() and np.max(np.abs(slope))<=.15:
                plane = slope[0]*x+slope[1]*y+fit.estimator_.intercept_
        except ValueError:
            pass
    result = np.stack([np.clip(lidar[2]-plane,-2,6),np.clip(low-plane,-2,6),
                       np.clip(lidar[4],0,6),np.clip(lidar[1],0,8),radius/40]).astype(np.float32)
    result[:,~valid] = 0
    return result


def occupancy_features(probabilities,geometry):
    p = np.clip(np.asarray(probabilities,np.float32),1e-7,1)
    if p.shape!=(17,200,200) or np.shape(geometry)!=(5,200,200) or not np.isfinite(p).all():
        raise ValueError("Invalid occupancy features.")
    obstacle,ground = p[list(OBSTACLE_CLASSES)].max(0),p[list(GROUND_CLASSES)].max(0)
    return np.concatenate([np.stack([np.log(obstacle)-np.log(ground),obstacle,ground,
                                    -(p*np.log(p)).sum(0)]),geometry],axis=0)


def validate_calibration(config):
    if config["version"]!=VERSION or tuple(config["features"])!=FEATURES:
        raise ValueError("Unsupported occupancy calibration.")
    for name in ("mean","scale","coef"):
        value = np.asarray(config[name],np.float64)
        if value.shape!=(len(FEATURES),) or not np.isfinite(value).all():
            raise ValueError("Invalid occupancy calibration coefficients.")
    if (np.asarray(config["scale"])<=0).any() or not np.isfinite(float(config["intercept"])):
        raise ValueError("Invalid occupancy calibration scale/intercept.")
    if not 0<float(config.get("strength",1))<=1:
        raise ValueError("Invalid occupancy calibration strength.")


def apply_calibration(prediction,config,*,lidar=None,geometry=None):
    validate_calibration(config)
    if geometry is None:
        geometry = geometry_features(lidar)
    features = occupancy_features(prediction["probabilities"],geometry)
    mean,scale,coef = (np.asarray(config[name],np.float32) for name in ("mean","scale","coef"))
    score = (coef[:,None,None]*(features-mean[:,None,None])/scale[:,None,None]).sum(0)+float(config["intercept"])
    score = np.clip(score,-8,8)
    delta = np.clip(score-features[0],-12,12)*float(config.get("strength",1))
    logits = np.log(np.clip(prediction["probabilities"].astype(np.float32),1e-7,1))
    logits[list(OBSTACLE_CLASSES)] += delta
    logits -= logits.max(0)
    p = np.exp(logits); p /= p.sum(0)
    valid = prediction["valid"].astype(bool)
    p[:,~valid] = prediction["probabilities"][:,~valid]
    result = dict(prediction,probabilities=p.astype(np.float16))
    return result


def fit_calibration(records,*,foreground_weight=1,C=1):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    xs,ys,weights = [],[],[]
    for record in records:
        labels = record["labels"]
        mask = record["prediction"]["valid"].astype(bool)&(labels!=255)&(labels!=0)
        indices = np.flatnonzero(mask)
        if not len(indices):
            continue
        if len(indices)>1500:
            indices = indices[np.linspace(0,len(indices)-1,1500).astype(int)]
        target = record["target_mask"]&mask&np.isin(labels,OBSTACLE_CLASSES)
        indices = np.union1d(indices,np.flatnonzero(target))
        features = occupancy_features(record["prediction"]["probabilities"],record["geometry"])
        xs.append(features.reshape(len(FEATURES),-1)[:,indices].T)
        ys.append(np.isin(labels.ravel()[indices],OBSTACLE_CLASSES))
        weights.append(np.where(target.ravel()[indices],foreground_weight,1.))
    x,y,w = np.concatenate(xs),np.concatenate(ys),np.concatenate(weights)
    scaler = StandardScaler().fit(x)
    model = LogisticRegression(C=C,class_weight="balanced",solver="lbfgs",max_iter=300,tol=1e-5)
    model.fit(scaler.transform(x),y,sample_weight=w)
    config = {"version":VERSION,"features":list(FEATURES),"mean":scaler.mean_.tolist(),
              "scale":scaler.scale_.tolist(),"coef":model.coef_[0].tolist(),
              "intercept":float(model.intercept_[0]),"strength":1.,
              "training_cells":len(y),"foreground_weight":foreground_weight,"C":C}
    validate_calibration(config)
    return config
