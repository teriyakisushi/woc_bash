import os
import sys

# Load Config
def load_config():
    cfg = {
        "api_base": os.environ.get("WOC_API_BASE", "https://api.deepseek.com"),
        "api_key": os.environ.get("WOC_API_KEY", ""),
        "model": os.environ.get("WOC_MODEL", "deepseek-flash"),
        "timeout": int(os.environ.get("WOC_TIMEOUT", "30")),
    }

    path = os.path.expanduser("~/.config/woc/config.toml")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if not v:
                    continue
                if k == "timeout":
                    try:
                        cfg[k] = int(v)
                    except ValueError:
                        print(f"[woc] 配置项 timeout 非法: {v!r}，已忽略", file=sys.stderr)
                elif k in cfg:
                    cfg[k] = v
    return cfg

