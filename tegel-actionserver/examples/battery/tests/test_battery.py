"""The ported actions, called directly and through a running action server."""

from __future__ import annotations

import logging

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tegel.actionserver import RunContext
from tegel.actionserver.app import create_app
from tegel.actionserver.auth import ApiKeyAuth
from tegel.actionserver.config import ActionServerConfig
from tegel.battery.trajectory import Trajectory
from tegel.battery.transport import msd_diffusion

H = {"Authorization": "Bearer battery-key"}


def _ctx(tmp_path) -> RunContext:
    return RunContext(run_id="t", action="t", principal="t", work_dir=tmp_path,
                      log=logging.getLogger("t"))


def random_walk(d_cm2s: float, n_frames=2000, n_atoms=64, dt_fs=10.0, seed=1) -> Trajectory:
    rng = np.random.default_rng(seed)
    sigma = np.sqrt(2 * (d_cm2s / 1e-4) * dt_fs / 1000.0)  # per-axis step, A
    steps = rng.normal(0, sigma, size=(n_frames, n_atoms, 3))
    return Trajectory(positions=np.cumsum(steps, axis=0), numbers=np.full(n_atoms, 3),
                      cell=np.eye(3) * 20, timestep_fs=dt_fs, temperature_K=600)


def test_msd_recovers_known_d_with_calibrated_error(tmp_path) -> None:
    true_d, z = 1e-5, []
    for seed in range(20):
        path = random_walk(true_d, seed=seed).save(tmp_path / f"w{seed}.npz")
        q = msd_diffusion(_ctx(tmp_path), path, species="Li")["diffusion_coefficient"]
        assert abs(q.value - true_d) / true_d < 0.3
        z.append((q.value - true_d) / q.uncertainty)
    assert 0.4 < np.std(z) < 2.5, np.std(z)


def test_msd_refuses_non_diffusive(tmp_path) -> None:
    rng = np.random.default_rng(0)
    still = Trajectory(positions=rng.normal(0, 0.1, size=(500, 32, 3)), numbers=np.full(32, 3),
                       cell=np.eye(3) * 10, timestep_fs=10.0)
    with pytest.raises(RuntimeError, match="not diffusive"):
        msd_diffusion(_ctx(tmp_path), still, species="Li")


@pytest.fixture
def server(tmp_path):
    from pymatgen.core import Lattice, Structure

    data = tmp_path / "data"
    data.mkdir()
    Structure(Lattice.cubic(3.615), ["Cu"] * 4,
              [[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]]).to(
        filename=str(data / "Cu.cif"))
    config = ActionServerConfig(public_origin="http://testserver", results_dir=tmp_path / "srv",
                                readable_storage=[data], resources={"gpu": 1})
    auth = ApiKeyAuth(keys=[{"key": "battery-key", "subject": "ana",
                             "scopes": ["actions:read", "actions:run"]}])
    app = create_app(config, auth=auth)  # actions from entry points
    with TestClient(app) as client:
        client.data = data
        yield client
    app.state.tegel_actions.close()


def _run(client, body) -> dict:
    response = client.post("/runs", json=body, headers=H)
    assert response.status_code == 202, response.text
    run = client.post(f"/runs/{response.json()['id']}/wait", params={"timeout": 300},
                      headers=H).json()
    assert run["state"] == "succeeded", run["error"]
    return run


def _parts(client, run) -> dict:
    resource = run["result"].rsplit("/", 1)[1]
    data = client.get(f"/resources/{resource}/data", headers=H).json()
    return {p["name"]: p["content_url"] for p in data["parts"]}


def _record(client, run) -> dict:
    resource = run["result"].rsplit("/", 1)[1]
    return client.get(f"/resources/{resource}", headers=H).json()["metadata"]


def test_match_recovers_a_lattice_expansion(server) -> None:
    import pandas as pd

    cif = (server.data / "Cu.cif").as_uri()
    powder = _run(server, {"action": "xrd.powder", "inputs": {"structure": cif}})
    url = _parts(server, powder)["pattern"].replace("http://testserver", "")
    sticks = pd.DataFrame(server.get(url, params={"format": "application/json"},
                                     headers=H).json())
    # A "measured" profile: the same reflections from a cell 1% larger, as Gaussian peaks.
    lam = 1.54184
    d = lam / (2 * np.sin(np.radians(sticks["two_theta"] / 2))) * 1.01
    centres = 2 * np.degrees(np.arcsin(lam / (2 * d)))
    x = np.linspace(30, 90, 6001)
    y = sum(h * np.exp(-0.5 * ((x - c) / 0.05) ** 2)
            for c, h in zip(centres, sticks["intensity"], strict=True))
    pd.DataFrame({"two_theta": x, "intensity": y}).to_csv(server.data / "measured.csv",
                                                          index=False)
    match = _run(server, {"action": "xrd.match",
                          "inputs": {"structure": cif,
                                     "measured": (server.data / "measured.csv").as_uri()}})
    quantities = _record(server, match)["quantities"]
    assert quantities["d_scale"]["numeric_value"] == pytest.approx(1.01, abs=2e-3)
    assert quantities["fraction_matched"]["numeric_value"] >= 0.75
    assert quantities["unexplained_peaks"]["numeric_value"] == 0


def test_relax_then_md_through_the_server(server) -> None:
    cif = (server.data / "Cu.cif").as_uri()
    relaxed = _run(server, {"action": "ase.relax", "inputs": {"structure": cif},
                            "parameters": {"calculator": "emt", "fmax": 0.01}})
    record = _record(server, relaxed)
    assert abs(record["quantities"]["volume_change"]["numeric_value"]) < 0.05
    structure = _parts(server, relaxed)["structure"]
    md = _run(server, {"action": "ase.md", "inputs": {"structure": structure},
                       "parameters": {"calculator": "emt", "steps": 100, "save_every": 10,
                                      "temperature_K": 300}})
    trajectory = _parts(server, md)["trajectory"]
    response = server.post("/runs", json={"action": "transport.msd_diffusion",
                                          "inputs": {"trajectory": trajectory},
                                          "parameters": {"species": "Cu"}}, headers=H)
    run = server.post(f"/runs/{response.json()['id']}/wait", params={"timeout": 60},
                      headers=H).json()
    # Ten frames of a solid: the action refuses rather than report a meaningless D.
    assert run["state"] == "failed" and "frames" in run["error"]
