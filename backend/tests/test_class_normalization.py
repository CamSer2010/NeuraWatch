"""NW-1102 AC: verify COCO class IDs map into the three NeuraWatch categories."""
import pytest

from app.services.inference_service import _CLASS_MAP, _TARGET_CLASSES


@pytest.mark.parametrize(
    "coco_id, expected",
    [
        (0, "person"),
        (1, "bicycle"),
        (2, "vehicle"),  # car
        (3, "vehicle"),  # motorcycle
        (5, "vehicle"),  # bus
        (7, "vehicle"),  # truck
    ],
)
def test_class_mapping(coco_id: int, expected: str) -> None:
    assert _CLASS_MAP[coco_id] == expected


@pytest.mark.parametrize(
    "coco_id",
    [4, 6, 8, 14, 16, 17, 18, 67],  # airplane, train, boat, bird, cat, dog, horse, cell phone
)
def test_unmapped_classes_excluded(coco_id: int) -> None:
    assert coco_id not in _CLASS_MAP


def test_target_classes_match_map_keys() -> None:
    # Guards against _TARGET_CLASSES and _CLASS_MAP drifting apart.
    assert set(_TARGET_CLASSES) == set(_CLASS_MAP.keys())
