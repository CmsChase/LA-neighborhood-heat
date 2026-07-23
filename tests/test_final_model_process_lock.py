from __future__ import annotations

from pathlib import Path

import pytest

from la_heat.final_model_process_lock import (
    FinalModelAlreadyRunning,
    exclusive_final_model_process,
)


def test_process_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    lock_path = tmp_path / "final-model.lock"

    with exclusive_final_model_process(lock_path):
        with pytest.raises(FinalModelAlreadyRunning):
            with exclusive_final_model_process(lock_path):
                pass

    with exclusive_final_model_process(lock_path):
        assert lock_path.is_file()
