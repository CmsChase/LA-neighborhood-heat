from __future__ import annotations

import pytest

from scripts.stage_daymet_grid import _validated_args


def test_prompt_token_requires_subset_download(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        _validated_args(["--prompt-token"])
    assert "--prompt-token requires --download-subsets" in capsys.readouterr().err


def test_prompt_token_download_arguments_are_accepted() -> None:
    args = _validated_args(["--download-subsets", "--prompt-token"])
    assert args.download_subsets is True
    assert args.prompt_token is True
