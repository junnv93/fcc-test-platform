"""Application orchestration for the two web sample inventory exports."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Optional

from fcc_test_contracts.common.api_error_codes import ErrorCode
from fcc_test_platform.application.central_sample_inventory_service import (
    SampleInventoryNotFoundError,
)
from fcc_test_kernel.domain.models.sample_inventory import SampleStatus
from fcc_test_platform.domain.ports.output.central_sample_inventory_read_port import (
    CentralSampleInventoryReadPort,
)
from fcc_test_platform.domain.ports.output.sample_inventory_export_port import SampleInventoryExportPort
from fcc_test_kernel.domain.services.sample_inventory_policy import (
    SampleInvalidFilter,
    normalize_status,
)


class SampleInventoryExportCategoryUnresolvedError(ValueError):
    """A selected sample cannot be assigned to a PM/RF template category."""

    error_code = ErrorCode.SAMPLE_EXPORT_CATEGORY_UNRESOLVED.value

    def __init__(self, sample_ids: list[str]) -> None:
        self.sample_ids = tuple(str(value) for value in sample_ids)
        super().__init__(
            f'cannot determine export category for sample ids: {", ".join(self.sample_ids)}'
        )


class SampleInventoryExportTemplateError(ValueError):
    """The requested export template is not part of the public contract."""


@dataclass(frozen=True)
class SampleInventoryExportResult:
    content: bytes
    filename: str
    template: str
    sample_ids: tuple[str, ...]
    content_type: str = (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


class SampleInventoryExportService:
    """Use the inventory list service's filter semantics, then render XLSX."""

    _TEMPLATES = frozenset({'pm-status', 'rf-data'})

    def __init__(self, inventory_service, read_port: CentralSampleInventoryReadPort,
                 renderer: SampleInventoryExportPort) -> None:
        self._inventory = inventory_service
        self._read = read_port
        self._renderer = renderer

    def export(
        self, project_id: str, template: str, *, team: Optional[str] = None,
        status: Optional[str] = None, as_of: Optional[str] = None,
        include_deleted: bool = False,
    ) -> SampleInventoryExportResult:
        if template not in self._TEMPLATES:
            raise SampleInventoryExportTemplateError(
                f'unsupported sample inventory export template: {template}'
            )
        project = self._read.get_project(project_id)
        if project is None:
            raise SampleInventoryNotFoundError(f'unknown project_id {project_id}')

        effective_status, effective_include_deleted = _status_filter(
            status, include_deleted,
        )
        items = self._all_items(
            project_id=project_id, team=team, status=effective_status,
            as_of=as_of, include_deleted=effective_include_deleted,
        )
        sample_ids = tuple(str(item['sample_id']) for item in items)
        categories = {sample_id: _export_category(item.get('test_category'))
                      for sample_id, item in zip(sample_ids, items)}
        unresolved = [sample_id for sample_id, category in categories.items() if category is None]
        if unresolved:
            raise SampleInventoryExportCategoryUnresolvedError(unresolved)

        intakes = self._intakes(project_id, sample_ids, as_of=as_of, items=items)
        if template == 'pm-status':
            records = [dict(item, project=project) for item in items]
        else:
            records = _rf_records(items, intakes, categories)
        content = self._renderer.render(template, records, project=project)
        model = str(project.get('model_name') or project.get('project_code') or 'project')
        return SampleInventoryExportResult(
            content=content,
            filename=_filename(model, template),
            template=template,
            sample_ids=sample_ids,
        )

    def _all_items(self, **filters: Any) -> list[dict]:
        items: list[dict] = []
        cursor = None
        while True:
            page = self._inventory.list_samples(
                **filters, after=cursor, limit=100,
            )
            items.extend(page.get('items') or [])
            cursor = page.get('next_cursor')
            if not cursor:
                return items

    def _intakes(self, project_id: str, sample_ids: tuple[str, ...], *,
                 as_of: Optional[str], items: list[dict]) -> dict[str, list[dict]]:
        if not sample_ids:
            return {}
        list_intakes = getattr(self._read, 'list_intakes', None)
        if callable(list_intakes):
            rows = list_intakes(project_id, list(sample_ids), as_of=as_of)
        else:
            # Test doubles and older read adapters can still render a correct
            # no-history row; production uses list_intakes for every intake row.
            rows = []
            for item in items:
                if item.get('latest_intake'):
                    rows.append(dict(item['latest_intake'], sample_id=item['sample_id']))
        result: dict[str, list[dict]] = {sample_id: [] for sample_id in sample_ids}
        for row in rows:
            sample_id = str(row.get('sample_id'))
            if sample_id in result:
                result[sample_id].append(dict(row))
        return result


def _status_filter(status: Optional[str], include_deleted: bool) -> tuple[Optional[str], bool]:
    if status in (None, '', 'all'):
        return None, True if status == 'all' else bool(include_deleted)
    try:
        normalized = normalize_status(status).value
    except ValueError as exc:
        raise SampleInvalidFilter(str(exc)) from exc
    return normalized, bool(include_deleted)


def _export_category(value: Any) -> Optional[str]:
    text = str(value or '').strip().casefold()
    if 'conduc' in text or 'conducted' in text:
        return 'Conduction'
    if any(token in text for token in ('radiat', 'radiated', 'sar', 'hac', 'mmwave')):
        return 'Radiation'
    return None


def _rf_records(items: list[dict], intakes: Mapping[str, list[dict]],
                categories: Mapping[str, Optional[str]]) -> list[dict]:
    records: list[dict] = []
    for item in items:
        sample_id = str(item['sample_id'])
        sample_intakes = list(intakes.get(sample_id) or [])
        if not sample_intakes:
            sample_intakes = [{}]
        for index, intake in enumerate(sample_intakes):
            records.append({
                'sample_id': sample_id,
                'sample_number': item.get('sample_number'),
                'test_category': categories[sample_id],
                'intake': intake,
                'radiation_tech_group': (
                    intake.get('tech_group') if index == 0 else None
                ),
            })
    return records


def _filename(model: str, template: str) -> str:
    safe_model = re.sub(r'[^A-Za-z0-9._-]+', '_', model).strip('._') or 'project'
    return f'sample-inventory-{safe_model}-{template}.xlsx'


__all__ = [
    'SampleInventoryExportCategoryUnresolvedError',
    'SampleInventoryExportResult',
    'SampleInventoryExportService',
    'SampleInventoryExportTemplateError',
]
