# infrastructure/excel/atomic_write.py
"""
Atomic Excel Write — 모든 xlsx 쓰기의 SSOT.

모든 xlsx 파일 교체는 이 모듈의 atomic_xlsx_write()를 통과해야 합니다.
직접 wb.save(실제경로) 호출은 아키텍처 위반입니다.

아키텍처 위치:
  infrastructure/excel/  (순수 I/O 모듈, 도메인 의존 없음)

소비자:
  excel_io.py          — safe_excel_operation() 내부
  excel_batch_saver.py — _openpyxl_save() 내부
  excel_wal_manager.py — DurableWAL._apply_wal_to_excel() 내부
  zip_sheet_patcher.py — patch_sheets() 내부
  correction_template_generator.py — create_correction_templates_in_excel() 내부

계약:
  - write_fn(temp_path)이 성공하면 → 원자적으로 target 교체 (os.replace)
  - write_fn이 실패하면 → target 무변경, temp 정리
  - ZIP 검증 실패하면 → target 무변경, temp 정리
  - 이 함수 바깥에서 wb.save(실제경로) 호출 금지
  - lock / backup / cache 무효화는 호출자 책임 (단일 책임 원칙)
"""

import os
import threading
import zipfile
from typing import Callable

from fcc_test_kernel.logger_config import get_logger

logger = get_logger('excel')


def _fsync_file(path: str) -> None:
    """
    파일 데이터를 디스크에 강제 플러시 (전원 손실 대비).

    Windows: FlushFileBuffers는 쓰기 핸들 필요 → O_RDWR 사용.
    Linux/macOS: O_RDONLY도 fsync 가능하나 통일성을 위해 O_RDWR 사용.
    """
    fd = os.open(path, os.O_RDWR | getattr(os, 'O_BINARY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class AtomicWriteError(Exception):
    """원자적 쓰기 실패 — 원본 파일은 보존됨."""


def atomic_xlsx_write(
    target_path: str,
    write_fn: Callable[[str], None],
    *,
    validate: bool = True,
    label: str = "",
) -> None:
    """
    모든 xlsx 쓰기의 SSOT.

    순수 "write → validate → replace" 만 담당합니다 (단일 책임).
    lock / backup / cache 무효화는 이 함수의 책임이 아닙니다.

    Args:
        target_path: 최종 저장될 xlsx 파일 경로
        write_fn:    temp_path를 받아 temp_path에 파일을 쓰는 콜백
        validate:    True이면 ZIP CRC 검증 후 교체 (기본값)
        label:       에러 로그에 포함될 컨텍스트 레이블

    Raises:
        AtomicWriteError: 쓰기 또는 검증 실패 시. 원본 파일은 보존됨.
    """
    temp_path = (
        f'{target_path}.atomic_{os.getpid()}_{threading.get_ident()}.tmp'
    )
    try:
        write_fn(temp_path)

        if validate:
            with zipfile.ZipFile(temp_path, 'r') as zf:
                bad = zf.testzip()
                if bad:
                    raise AtomicWriteError(
                        f"ZIP CRC 실패: {bad}"
                        + (f" [{label}]" if label else "")
                    )

        # 전원 손실 시 temp 데이터 디스크 보장 (A1)
        _fsync_file(temp_path)

        os.replace(temp_path, target_path)

    except AtomicWriteError:
        raise
    except Exception as exc:
        raise AtomicWriteError(
            f"[{label}] {exc}" if label else str(exc)
        ) from exc
    finally:
        # temp_path가 남아있으면 제거 (replace 성공 시에는 이미 없음)
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def cleanup_orphan_temps(directory: str) -> int:
    """
    시작 시 이전 실행의 orphan atomic temp 파일을 정리합니다.

    프로세스가 비정상 종료되어 '.atomic_<pid>_<tid>.tmp' 패턴의 임시 파일이
    남아있는 경우 삭제합니다.

    Returns:
        정리된 파일 수
    """
    cleaned = 0
    try:
        for fname in os.listdir(directory):
            if '.atomic_' in fname and fname.endswith('.tmp'):
                fpath = os.path.join(directory, fname)
                try:
                    os.remove(fpath)
                    cleaned += 1
                    logger.info(f"Cleaned orphan atomic temp: {fname}")
                except OSError as e:
                    logger.warning(
                        f"Could not remove orphan temp {fname}: {e}"
                    )
    except Exception as e:
        logger.warning(f"cleanup_orphan_temps error in {directory!r}: {e}")
    return cleaned
