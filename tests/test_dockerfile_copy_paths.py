"""Dockerfile 의 ``COPY`` 원본이 저장소에 실재하는가 (2026-09-03).

**이 봉인이 존재하는 이유는 이 웨이브가 만든 결함이다.** 공유 커널 1단계에서
`logger_config.py` 를 다른 배포판으로 옮기고 저장소에서 지웠는데,
`infra/central/Dockerfile.api` 의 ``COPY logger_config.py ./`` 를 함께 지우지 않았다.

실측: 이미지 빌드가 ``failed to compute cache key … "/logger_config.py": not found``
로 죽었다. **그리고 그 실패를 이 웨이브의 게이트 어느 것도 잡지 못했다** —

    check_shared_kernel_closure   import 폐포를 본다
    check_import_name_ownership   설치된 배포판을 본다
    lane_check / pytest           파이썬 코드를 본다

셋 다 *Dockerfile 이 무엇을 복사하는가* 라는 축을 갖지 않는다. 파이썬 축에서는
파일이 사라진 것이 **정상**이고(그것이 이관의 목적이다) 빌드 축에서만 결함이다.
`.claude/rules/check-axis-blindness.md` 의 서식 그대로 — 「내 축이 못 보는 것인가」.

⚠️ 이 축은 **빌드를 돌리지 않고** 판정한다. 빌드는 느리고 네트워크를 타므로 게이트로
쓰면 꺼진다. COPY 원본의 존재는 파일시스템만으로 답할 수 있다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILES = sorted((_REPO_ROOT / 'infra').rglob('Dockerfile*'))

#: ``COPY [--flag …] <src>… <dst>`` — 마지막 인자가 목적지다.
_COPY_RE = re.compile(r'^\s*COPY\s+(?P<rest>.+?)\s*$', re.IGNORECASE)


def copy_sources(dockerfile: Path) -> list[tuple[int, str]]:
    """``COPY`` 의 **원본**만 낸다. 목적지와 플래그는 대상이 아니다."""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(dockerfile.read_text(encoding='utf-8').splitlines(), 1):
        m = _COPY_RE.match(line)
        if not m:
            continue
        raw = m.group('rest').split()
        # ⚠️ `COPY --from=<stage>` 의 원본은 **이전 빌드 단계 안**이지 저장소가 아니다.
        # 저장소에서 찾으면 실재하는 멀티스테이지 빌드가 전부 red 가 되고, 그런 게이트는
        # 삭제된다. 판정 대상은 «빌드 컨텍스트에서 오는 COPY» 뿐이다.
        if any(f.startswith('--from=') for f in raw if f.startswith('--')):
            continue
        parts = [p for p in raw if not p.startswith('--')]
        if len(parts) < 2:
            continue  # 목적지만 있는 형태는 판정 대상이 아니다
        out.extend((lineno, src) for src in parts[:-1])
    return out


class TestEveryCopySourceExists(unittest.TestCase):
    def test_dockerfiles_are_found(self):
        """비-공허성 — Dockerfile 을 하나도 못 찾으면 이 봉인은 아무것도 묻지 않는다."""
        self.assertTrue(_DOCKERFILES, 'infra/ 에서 Dockerfile 을 찾지 못했다')

    def test_copy_sources_are_found(self):
        """비-공허성 — COPY 를 하나도 못 찾으면 정규식이 깨진 것이다."""
        total = sum(len(copy_sources(d)) for d in _DOCKERFILES)
        self.assertTrue(total, 'Dockerfile 에서 COPY 원본을 하나도 찾지 못했다')

    def test_every_copy_source_exists_in_the_repo(self):
        missing = []
        for dockerfile in _DOCKERFILES:
            for lineno, src in copy_sources(dockerfile):
                # 빌드 컨텍스트는 저장소 루트다(compose 의 `context: ..`).
                target = _REPO_ROOT / src.rstrip('/')
                if not (target.exists() or list(_REPO_ROOT.glob(src.rstrip('/')))):
                    rel = dockerfile.relative_to(_REPO_ROOT)
                    missing.append(f'{rel}:{lineno} → {src}')
        self.assertFalse(
            missing,
            '이 COPY 원본이 저장소에 없다 — 이미지 빌드가 '
            '"failed to compute cache key … not found" 로 죽는다:\n  '
            + '\n  '.join(missing),
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
