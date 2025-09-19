import yaml, os

def load_config(path="config.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def ensure_dirs(cfg):
    import os
    lp = cfg["logging"]
    for key in ("csv_path", "sqlite_path"):
        d = os.path.dirname(lp[key])
        if d:
            os.makedirs(d, exist_ok=True)
