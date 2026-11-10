"""Bounded, invisible child processes for local media work."""
import os
import subprocess


def process_options():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    return {}


def bounded_env(env=None):
    env = dict(os.environ if env is None else env)
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        try:
            count = int(env.get(key, "4"))
        except ValueError:
            count = 4
        env[key] = str(max(1, min(4, count)))
    return env
