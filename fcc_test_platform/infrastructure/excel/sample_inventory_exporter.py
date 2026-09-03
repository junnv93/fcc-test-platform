"""openpyxl renderer for the sanitized PM and RF sample templates."""
from __future__ import annotations

from copy import copy
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

import openpyxl

from fcc_test_kernel.infrastructure.excel.atomic_write import atomic_xlsx_write


PM_HEADERS = (
    '', 'Model name', 'Sample', 'Sample Description', '라벨넘버', 'SMSN',
    'S/N or\nIMEI', '반입증', 'TEAM', '발신자/연락처', '수신자/연락처',
    '수령한 날짜', '반출한 날짜', 'Note', '',
)
RF_HEADERS = (
    '시료번호', 'BL', 'AP', 'CP', 'CSC', 'RF CAL', 'HW Rev', '비고', '입고일',
)
RF_KEYSTRING = '*#12580*369#'
XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
TEMPLATE_DIRECTORY = (
    Path(__file__).resolve().parents[3] / 'tests' / 'fixtures' / 'sample_inventory_templates'
)
TEMPLATE_FILES = {
    'pm-status': 'pm_sample_status.xlsx',
    'rf-data': 'rf_sample_data.xlsx',
}


class SampleInventoryExcelExporter:
    """Render a workbook to bytes after atomic write + ZIP validation."""

    def render(self, template: str, records: Sequence[Mapping[str, Any]], *,
               project: Mapping[str, Any]) -> bytes:
        with tempfile.TemporaryDirectory(prefix='fcc-sample-export-') as directory:
            target = Path(directory) / 'sample-inventory.xlsx'
            atomic_xlsx_write(
                str(target),
                lambda temporary: self._write(temporary, template, records, project),
                validate=True,
                label='SampleInventoryExcelExporter',
            )
            return target.read_bytes()

    def _write(self, temp_path: str, template: str, records: Sequence[Mapping[str, Any]],
               project: Mapping[str, Any]) -> None:
        if template == 'pm-status':
            _write_pm(temp_path, records, project)
        elif template == 'rf-data':
            _write_rf(temp_path, records, project)
        else:
            raise ValueError(f'unsupported sample inventory export template: {template}')


def _write_pm(temp_path: str, records: Sequence[Mapping[str, Any]], project: Mapping[str, Any]) -> None:
    model = str(project.get('model_name') or project.get('project_code') or 'Samples')
    workbook = _load_template('pm-status')
    worksheet = workbook.active
    worksheet.title = _sheet_name(model)
    _replace_model_in_title(worksheet, model)
    # Keep the operational A:O footprint even though the final column is a
    # deliberately blank template column and openpyxl otherwise drops it.
    worksheet.cell(row=2, column=15).value = ''
    styles, row_height = _clear_dynamic_rows(worksheet, start_row=4)
    for index, sample in enumerate(records):
        sample_number = sample.get('sample_number') or ''
        category = sample.get('test_category') or ''
        row = worksheet.max_row + 1
        worksheet.append([
            None,
            model if index == 0 else None,
            'Device',
            f'{model}_{category} {sample_number}'.strip(),
            sample.get('label_number'),
            sample.get('smsn'),
            sample.get('serial_number'),
            sample.get('intake_cert'),
            sample.get('assigned_team'),
            sample.get('sender'),
            sample.get('receiver'),
            _excel_date(sample.get('received_date')),
            _excel_date(sample.get('released_date')),
            sample.get('note'),
            None,
        ])
        _copy_row_style(worksheet, row, styles, row_height)
    workbook.save(temp_path)
    workbook.close()


def _write_rf(temp_path: str, records: Sequence[Mapping[str, Any]], project: Mapping[str, Any]) -> None:
    workbook = _load_template('rf-data')
    for sheet_name in ('Conduction', 'Radiation'):
        worksheet = workbook[sheet_name]
        styles, row_height = _clear_dynamic_rows(worksheet, start_row=3)
        for record in records:
            if record.get('test_category') != sheet_name:
                continue
            intake = dict(record.get('intake') or {})
            row = worksheet.max_row + 1
            worksheet.append([
                record.get('sample_number'), intake.get('bl'), intake.get('ap'),
                intake.get('cp'), intake.get('csc'), intake.get('rf_cal'),
                intake.get('hw_rev'), intake.get('note'),
                _excel_date(intake.get('intake_date')),
                record.get('radiation_tech_group') if sheet_name == 'Radiation' else None,
            ][:10 if sheet_name == 'Radiation' else 9])
            _copy_row_style(worksheet, row, styles, row_height)
    workbook.save(temp_path)
    workbook.close()


def _load_template(template: str):
    filename = TEMPLATE_FILES[template]
    path = TEMPLATE_DIRECTORY / filename
    if not path.is_file():
        raise FileNotFoundError(f'sample inventory export template is missing: {path}')
    workbook = openpyxl.load_workbook(path, data_only=False, keep_links=True)
    # openpyxl normalizes empty inline strings to numeric empty cells on save.
    # Retain the template's empty-cell type so the structural replica remains
    # semantically stable across the render round trip.
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                if cell.value is None and cell.data_type == 'inlineStr':
                    cell.value = ''
    return workbook


def _replace_model_in_title(worksheet, model: str) -> None:
    title = worksheet['B1'].value
    if isinstance(title, str) and title:
        worksheet['B1'] = title.replace('SM-TEST1', model)


def _clear_dynamic_rows(worksheet, *, start_row: int):
    styles = [copy(cell._style) for cell in worksheet[start_row]]
    row_dimension = worksheet.row_dimensions[start_row]
    row_height = row_dimension.height
    if worksheet.max_row >= start_row:
        worksheet.delete_rows(start_row, worksheet.max_row - start_row + 1)
    return styles, row_height


def _copy_row_style(worksheet, row: int, styles, row_height) -> None:
    if row_height is not None:
        worksheet.row_dimensions[row].height = row_height
    for column, style in enumerate(styles, start=1):
        worksheet.cell(row=row, column=column)._style = copy(style)


def _excel_date(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return value
    return value


def _sheet_name(value: str) -> str:
    cleaned = ''.join('_' if char in '[]:*?/\\' else char for char in value)
    return (cleaned[:31] or 'Samples').strip("'") or 'Samples'


__all__ = [
    'PM_HEADERS',
    'RF_HEADERS',
    'RF_KEYSTRING',
    'SampleInventoryExcelExporter',
    'XLSX_CONTENT_TYPE',
]
