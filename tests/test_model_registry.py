from pathlib import Path
from unittest import mock

from core import model_registry


def test_local_registry_crud_when_supabase_unavailable(tmp_path: Path) -> None:
    local_path = tmp_path / "model_registry_local.json"
    with mock.patch.object(model_registry, "_LOCAL_REGISTRY_PATH", local_path), mock.patch.object(
        model_registry, "_client", return_value=None
    ):
        created = model_registry.create_model(
            "local-v1",
            "Local Model",
            "spread",
            {"weights": {"torvik_adjem": 1.0}},
        )
        assert created is True

        models = model_registry.list_all_models("spread")
        assert len(models) == 1
        assert models[0]["model_id"] == "local-v1"

        assert model_registry.activate_model("local-v1") is True
        active = model_registry.get_active_model("spread")
        assert active["model_id"] == "local-v1"
        assert bool(active["is_active"]) is True

        assert model_registry.delete_model("local-v1") is True
        assert model_registry.list_all_models("spread") == []
