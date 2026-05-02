"""IMMA Phase 1 매칭 파이프라인 — 룩업 테이블 JSON 로더.

모듈 레벨에서 한 번만 JSON을 로드하고,
standard 이름으로 해당 테이블의 data를 반환하는 유틸리티를 제공한다.
"""

import json
from pathlib import Path
from functools import lru_cache

from config import LOOKUP_TABLE_PATH


@lru_cache(maxsize=1)
def _load_all() -> dict[str, list[dict]]:
    """룩업 JSON 전체를 로드하여 {standard: [object, ...]} 딕셔너리로 캐싱한다.

    동일 standard 이름의 object가 여러 개일 수 있으므로(e.g. KS_B_ISO_2768_1의
    선형/각도/모따기 세 테이블) 리스트로 저장한다.
    """
    raw = json.loads(Path(LOOKUP_TABLE_PATH).read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {}
    for obj in raw["objects"]:
        result.setdefault(obj["standard"], []).append(obj)
    return result


def get_table(standard: str, index: int = 0) -> list[dict]:
    """지정한 standard의 data 배열을 반환한다.

    Args:
        standard: 테이블 식별자
        index: 동일 standard가 여러 개일 때 인덱스 (기본 0 = 첫 번째)

    Returns:
        data 배열. 없으면 빈 리스트.
    """
    objs = _load_all().get(standard, [])
    if index < len(objs):
        return objs[index].get("data", [])
    return []


def get_all_tables(standard: str) -> list[list[dict]]:
    """동일 standard의 모든 data 배열을 리스트로 반환한다."""
    objs = _load_all().get(standard, [])
    return [obj.get("data", []) for obj in objs]
