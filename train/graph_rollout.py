"""CUDA-graph capture of one closed-loop step (policy + SGO + plant + EKF).

The environment updates its state by re-binding attributes (functional style).
In a captured graph every replay would re-read the pre-capture tensors, so the
state would never advance. GraphStep therefore copies each re-bound state
tensor back into its original buffer at the end of the captured step; the copy
is part of the graph, so a replay advances the state in place.

The step is ~6x faster than eager on the laptop GPU (launch overhead removed).
Correctness is checked against an eager twin (check_graph_matches_eager).
"""
from __future__ import annotations

import torch


def _state_objects(env):
    return [env, env.ekf] + ([env.feat] if hasattr(env, "feat") else [])


class GraphStep:
    def __init__(self, env, fn, generators=(), warmup: int = 3):
        """fn(env) runs one closed-loop step on env and returns a dict of tensors."""
        self.env = env
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            for _ in range(warmup):
                fn(env)
        torch.cuda.current_stream().wait_stream(s)
        pre = [(o, k, v) for o in _state_objects(env) for k, v in list(vars(o).items()) if torch.is_tensor(v)]
        self.graph = torch.cuda.CUDAGraph()
        for g in (env.g, *generators):
            self.graph.register_generator_state(g)
        with torch.cuda.graph(self.graph):
            out = fn(env)
            for o, k, v in pre:
                new = getattr(o, k)
                if new is not v:
                    if new.shape != v.shape or new.dtype != v.dtype:
                        raise RuntimeError(f"state {k} changed shape/dtype inside the step")
                    v.copy_(new)
                    setattr(o, k, v)
        # dict-held tensors (env.info) were created inside the capture: they are graph outputs
        self.out = out

    def __call__(self):
        self.graph.replay()
        return self.out


def check_graph_matches_eager(make_env, fn, steps: int = 300) -> float:
    """Max |difference| of the step outputs between a graph env and an eager twin."""
    eg, ee = make_env(), make_env()
    gs = GraphStep(eg, fn)
    for _ in range(3):                                   # the eager twin also runs the warm-up steps
        fn(ee)
    worst = 0.0
    for _ in range(steps):
        og = gs()
        oe = fn(ee)
        for k in og:
            worst = max(worst, float((og[k].float() - oe[k].float()).abs().max()))
    return worst
