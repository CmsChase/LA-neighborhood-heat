"""Deterministic quality selection for already aligned Landsat scene arrays."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True, slots=True)
class MosaicResult:
    """One selected source and its values for every covered output pixel.

    ``selected_scene_index`` refers to the input order. An uncovered pixel has
    index ``-1``, an empty scene ID, and missing numeric values. A covered but
    QA-invalid pixel retains its selected source and quality metadata, while its
    selected ST value is missing so it cannot accidentally become a target.
    """

    selected_scene_index: NDArray[np.int32]
    selected_scene_id: NDArray[np.str_]
    selected_st_value: NDArray[np.float64]
    selected_valid: NDArray[np.bool_]
    selected_st_qa: NDArray[np.float64]
    selected_cdist: NDArray[np.float64]
    footprint: NDArray[np.bool_]

    @property
    def covered_pixel_count(self) -> int:
        """Count union-footprint pixels once, regardless of scene overlap."""

        return int(np.count_nonzero(self.footprint))

    @property
    def valid_pixel_count(self) -> int:
        """Count selected QA-valid pixels once, regardless of scene overlap."""

        return int(np.count_nonzero(self.selected_valid))


def _require_shape(name: str, values: ArrayLike, shape: tuple[int, ...]) -> np.ndarray:
    array = np.asarray(values)
    if array.shape != shape:
        raise ValueError(f"{name} has shape {array.shape}; expected {shape}.")
    return array


def mosaic_aligned_scenes(
    *,
    scene_ids: Sequence[str],
    st_values: ArrayLike,
    qa_valid: ArrayLike,
    st_qa: ArrayLike,
    cdist: ArrayLike,
    footprint: ArrayLike,
) -> MosaicResult:
    """Select one source per pixel from aligned scene stacks.

    Every input array has shape ``(scene, ...pixels...)``. ``st_values`` may
    contain raw surface-temperature digital numbers or already converted LST;
    its units are preserved in ``selected_st_value``. Selection is independent
    of the temperature value and follows this locked precedence:

    1. QA-valid over QA-invalid;
    2. lower ST_QA;
    3. larger cloud distance (CDIST);
    4. lexically smaller scene ID.

    Non-finite ST_QA and CDIST values rank worst. Only pixels inside a scene's
    footprint participate. Scene stacks must already share an identical grid;
    this function never resamples or reprojects them.
    """

    identifiers = tuple(scene_ids)
    if not identifiers:
        raise ValueError("At least one scene is required.")
    if any(not isinstance(scene_id, str) or not scene_id for scene_id in identifiers):
        raise ValueError("Every scene ID must be a non-empty string.")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Scene IDs must be unique.")

    st_stack = np.asarray(st_values)
    if st_stack.ndim < 2:
        raise ValueError("Scene arrays must have shape (scene, ...pixels...).")
    if st_stack.shape[0] != len(identifiers):
        raise ValueError(
            f"Received {len(identifiers)} scene IDs for {st_stack.shape[0]} scene arrays."
        )
    if not np.issubdtype(st_stack.dtype, np.number):
        raise TypeError("st_values must be numeric.")

    stack_shape = st_stack.shape
    valid_stack = _require_shape("qa_valid", qa_valid, stack_shape)
    footprint_stack = _require_shape("footprint", footprint, stack_shape)
    if valid_stack.dtype != np.bool_:
        raise TypeError("qa_valid must be a boolean array.")
    if footprint_stack.dtype != np.bool_:
        raise TypeError("footprint must be a boolean array.")
    if np.any(valid_stack & ~footprint_stack):
        raise ValueError("QA-valid pixels cannot lie outside their scene footprint.")

    st_stack = st_stack.astype(np.float64, copy=False)
    if np.any(valid_stack & ~np.isfinite(st_stack)):
        raise ValueError("QA-valid pixels must have finite ST values.")

    st_qa_stack = _require_shape("st_qa", st_qa, stack_shape)
    cdist_stack = _require_shape("cdist", cdist, stack_shape)
    if not np.issubdtype(st_qa_stack.dtype, np.number):
        raise TypeError("st_qa must be numeric.")
    if not np.issubdtype(cdist_stack.dtype, np.number):
        raise TypeError("cdist must be numeric.")
    st_qa_stack = st_qa_stack.astype(np.float64, copy=False)
    cdist_stack = cdist_stack.astype(np.float64, copy=False)

    pixel_shape = stack_shape[1:]
    selected_index = np.full(pixel_shape, -1, dtype=np.int32)
    selected_valid = np.zeros(pixel_shape, dtype=bool)
    best_st_qa = np.full(pixel_shape, np.inf, dtype=np.float64)
    best_cdist = np.full(pixel_shape, -np.inf, dtype=np.float64)
    best_scene_rank = np.full(pixel_shape, len(identifiers), dtype=np.int32)

    lexical_rank = {
        scene_id: rank for rank, scene_id in enumerate(sorted(identifiers))
    }
    for scene_index, scene_id in enumerate(identifiers):
        candidate = footprint_stack[scene_index]
        candidate_valid = valid_stack[scene_index]
        candidate_st_qa = np.where(
            np.isfinite(st_qa_stack[scene_index]), st_qa_stack[scene_index], np.inf
        )
        candidate_cdist = np.where(
            np.isfinite(cdist_stack[scene_index]), cdist_stack[scene_index], -np.inf
        )
        candidate_rank = lexical_rank[scene_id]

        same_validity = candidate_valid == selected_valid
        same_st_qa = candidate_st_qa == best_st_qa
        same_cdist = candidate_cdist == best_cdist
        better = candidate & (
            (selected_index < 0)
            | (candidate_valid & ~selected_valid)
            | (
                same_validity
                & (
                    (candidate_st_qa < best_st_qa)
                    | (
                        same_st_qa
                        & (
                            (candidate_cdist > best_cdist)
                            | (same_cdist & (candidate_rank < best_scene_rank))
                        )
                    )
                )
            )
        )

        selected_index[better] = scene_index
        selected_valid[better] = candidate_valid[better]
        best_st_qa[better] = candidate_st_qa[better]
        best_cdist[better] = candidate_cdist[better]
        best_scene_rank[better] = candidate_rank

    covered = selected_index >= 0
    safe_index = np.maximum(selected_index, 0)[np.newaxis, ...]
    selected_st_value = np.take_along_axis(st_stack, safe_index, axis=0)[0]
    selected_st_value[~selected_valid] = np.nan

    selected_st_qa = np.take_along_axis(st_qa_stack, safe_index, axis=0)[0]
    selected_cdist = np.take_along_axis(cdist_stack, safe_index, axis=0)[0]
    selected_st_qa[~covered] = np.nan
    selected_cdist[~covered] = np.nan

    id_dtype = f"<U{max(len(scene_id) for scene_id in identifiers)}"
    selected_scene_id = np.full(pixel_shape, "", dtype=id_dtype)
    identifier_array = np.asarray(identifiers, dtype=id_dtype)
    selected_scene_id[covered] = identifier_array[selected_index[covered]]

    return MosaicResult(
        selected_scene_index=selected_index,
        selected_scene_id=selected_scene_id,
        selected_st_value=selected_st_value,
        selected_valid=selected_valid,
        selected_st_qa=selected_st_qa,
        selected_cdist=selected_cdist,
        footprint=covered,
    )
