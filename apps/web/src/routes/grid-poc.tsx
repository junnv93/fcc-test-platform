import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Button, PageHeader, SectionBand, StatusBadge, Toolbar } from '@/ui';

import {
  createGridPocRows,
  type GridPocRow,
  type GridPocTechnology,
  validateGridPocRow,
} from './grid-poc.fixture';

import type { ReactNode } from 'react';

type EditableKey = 'testItem' | 'channel' | 'targetPowerDbm' | 'limitDbm' | 'sampleCount';

interface DraftChange {
  readonly rowId: string;
  readonly field: string;
  readonly before: string;
  readonly after: string;
}

interface ActiveCell {
  readonly rowId: string;
  readonly field: EditableKey;
}

interface EditableColumnMeta {
  readonly key: EditableKey;
  readonly header: string;
}

const TECHNOLOGIES: readonly GridPocTechnology[] = ['BLE', 'BT', 'DTS', 'UNII'];

// PASTE_FIELDS 의 순서가 grid 의 arrow-key 이동 순서 + paste 의 column 매핑 순서를
// 동시에 결정한다. 두 의미가 같은 한 SSOT. 둘이 갈라지는 순간 별 상수로 분리한다.
const EDITABLE_COLUMNS: readonly EditableColumnMeta[] = [
  { key: 'testItem', header: 'Test item' },
  { key: 'channel', header: 'Channel' },
  { key: 'targetPowerDbm', header: 'Target dBm' },
  { key: 'limitDbm', header: 'Limit dBm' },
  { key: 'sampleCount', header: 'Samples' },
];
const PASTE_FIELDS: readonly EditableKey[] = EDITABLE_COLUMNS.map((column) => column.key);
const EDITABLE_HEADER_BY_KEY: ReadonlyMap<EditableKey, string> = new Map(
  EDITABLE_COLUMNS.map(({ key, header }) => [key, header]),
);

function isEditableKey(value: string): value is EditableKey {
  return (PASTE_FIELDS as readonly string[]).includes(value);
}

function clamp(value: number, min: number, max: number): number {
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

function describeRowForA11y(row: GridPocRow): string {
  // Screen-reader 사용자에게 의미 있는 row identifier. row.id (row-0001) 는
  // grid 의 internal handle 일 뿐이라 testItem + technologies 가 더 적절하다.
  const techs = row.technologies.join('/');
  return `${row.testItem} ${techs}`;
}

export default function GridPocRoute(): JSX.Element {
  const [technology, setTechnology] = useState<GridPocTechnology>('BLE');
  const [rows, setRows] = useState<GridPocRow[]>(() => createGridPocRows());
  const [activeCell, setActiveCell] = useState<ActiveCell | null>(null);
  const [isEditing, setIsEditing] = useState<boolean>(false);
  const [changes, setChanges] = useState<DraftChange[]>([]);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const activeCellRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const visibleRows = useMemo(
    () => rows.filter((row) => row.technologies.includes(technology)),
    [rows, technology],
  );

  const rowIssues = useMemo(() => {
    const next = new Map<string, readonly string[]>();
    for (const row of visibleRows) {
      next.set(row.id, validateGridPocRow(row));
    }
    return next;
  }, [visibleRows]);

  const columns = useMemo<ColumnDef<GridPocRow>[]>(
    () => [
      {
        accessorKey: 'id',
        header: 'Row',
        cell: (ctx) => <span className="grid-poc__mono">{ctx.getValue<string>()}</span>,
      },
      {
        accessorKey: 'technologies',
        header: 'Tech',
        cell: (ctx) => ctx.getValue<readonly string[]>().join(', '),
      },
      editableColumn('testItem'),
      {
        accessorKey: 'band',
        header: 'Band',
      },
      {
        accessorKey: 'modulation',
        header: 'Modulation',
      },
      editableColumn('channel'),
      {
        accessorKey: 'bandwidthMhz',
        header: 'BW MHz',
        cell: (ctx) => (
          <span className="grid-poc__number">{ctx.getValue<number | null>() ?? ''}</span>
        ),
      },
      editableColumn('targetPowerDbm', 'number'),
      editableColumn('limitDbm', 'number'),
      editableColumn('sampleCount', 'number'),
      {
        accessorKey: 'verdict',
        header: 'State',
        cell: (ctx) => {
          const value = ctx.getValue<GridPocRow['verdict']>();
          const status = value === 'ready' ? 'pass' : value === 'warning' ? 'stale' : 'fail';
          return <StatusBadge status={status} label={value} />;
        },
      },
      {
        id: 'issues',
        header: 'Issues',
        cell: (ctx) => {
          const issues = rowIssues.get(ctx.row.original.id) ?? [];
          return issues.length === 0 ? (
            <span className="grid-poc__muted">none</span>
          ) : (
            <span className="grid-poc__issue">{issues.join('; ')}</span>
          );
        },
      },
    ],
    [rowIssues],
  );

  const table = useReactTable({
    data: visibleRows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
  });
  const tableRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => gridRef.current,
    estimateSize: () => 44,
    overscan: 8,
  });
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // WAI-ARIA grid roving tabindex SSOT: activeCell 은 visible 영역 안에 1개만
  // 존재해야 한다. technology 전환 / row 제거 / 초기 mount 시 invariant 유지.
  useEffect(() => {
    if (visibleRows.length === 0) {
      if (activeCell !== null) setActiveCell(null);
      return;
    }
    const stillVisible = activeCell
      ? visibleRows.some((row) => row.id === activeCell.rowId)
      : false;
    if (!activeCell || !stillVisible) {
      const firstRow = visibleRows[0];
      const firstField = PASTE_FIELDS[0];
      if (firstRow && firstField) {
        setActiveCell({ rowId: firstRow.id, field: firstField });
        setIsEditing(false);
      }
    }
  }, [visibleRows, activeCell]);

  // 활성 cell 의 DOM ref 가 마운트되면 focus 이동 — virtualizer 가 row 를 unmount
  // 했다가 다시 mount 하는 케이스(스크롤)도 자동 복원. isEditing 중에는 input 이
  // focus 라 cell focus 는 건너뛴다.
  useEffect(() => {
    if (isEditing) return;
    const node = activeCellRef.current;
    if (node && document.activeElement !== node) {
      node.focus({ preventScroll: true });
    }
  }, [activeCell, isEditing]);

  // 활성 cell row 가 가상화 viewport 밖이면 자동 scroll 로 노출. 키보드 navigation
  // 이 viewport 를 벗어나는 사용자 보고 사각지대 회피.
  useEffect(() => {
    if (!activeCell) return;
    const index = visibleRows.findIndex((row) => row.id === activeCell.rowId);
    if (index < 0) return;
    virtualizer.scrollToIndex(index, { align: 'auto' });
  }, [activeCell, visibleRows, virtualizer]);

  function updateCell(rowId: string, field: EditableKey, rawValue: string): void {
    setRows((current) =>
      current.map((row) => {
        if (row.id !== rowId) return row;
        const before = String(row[field] ?? '');
        const value = parseEditableValue(field, rawValue);
        const after = String(value ?? '');
        setChanges((items) => [{ rowId, field, before, after }, ...items].slice(0, 12));
        return { ...row, [field]: value };
      }),
    );
  }

  function handlePaste(event: React.ClipboardEvent<HTMLDivElement>): void {
    if (!activeCell) return;
    const text = event.clipboardData.getData('text/plain');
    if (!text.trim()) return;
    event.preventDefault();

    const matrix = text
      .trimEnd()
      .split(/\r?\n/)
      .map((line) => line.split('\t'));
    const startRow = visibleRows.findIndex((row) => row.id === activeCell.rowId);
    const startColumn = PASTE_FIELDS.indexOf(activeCell.field);
    if (startRow < 0 || startColumn < 0) return;

    setRows((current) => {
      const next = [...current];
      const nextChanges: DraftChange[] = [];
      for (let rowOffset = 0; rowOffset < matrix.length; rowOffset += 1) {
        const visibleTarget = visibleRows[startRow + rowOffset];
        if (!visibleTarget) continue;
        const currentIndex = next.findIndex((row) => row.id === visibleTarget.id);
        if (currentIndex < 0) continue;
        let targetRow = next[currentIndex];
        if (!targetRow) continue;
        const line = matrix[rowOffset] ?? [];
        for (let colOffset = 0; colOffset < line.length; colOffset += 1) {
          const field = PASTE_FIELDS[startColumn + colOffset];
          if (!field) continue;
          const before = String(targetRow[field] ?? '');
          const value = parseEditableValue(field, line[colOffset] ?? '');
          const after = String(value ?? '');
          targetRow = { ...targetRow, [field]: value };
          nextChanges.push({ rowId: targetRow.id, field, before, after });
        }
        next[currentIndex] = targetRow;
      }
      setChanges((items) => [...nextChanges.reverse(), ...items].slice(0, 12));
      return next;
    });
  }

  function handleDragEnd(event: DragEndEvent): void {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const fromVisible = visibleRows.findIndex((row) => row.id === active.id);
    const toVisible = visibleRows.findIndex((row) => row.id === over.id);
    if (fromVisible < 0 || toVisible < 0) return;
    const reorderedVisible = arrayMove(visibleRows, fromVisible, toVisible);
    setRows((current) => {
      const byId = new Map(reorderedVisible.map((row, index) => [row.id, index]));
      return [...current].sort((a, b) => {
        const aOrder = byId.get(a.id);
        const bOrder = byId.get(b.id);
        if (aOrder == null && bOrder == null) return 0;
        if (aOrder == null) return 1;
        if (bOrder == null) return -1;
        return aOrder - bOrder;
      });
    });
  }

  const moveActiveCell = useCallback(
    (rowDelta: number, columnDelta: number): void => {
      if (!activeCell || visibleRows.length === 0) return;
      const rowIndex = visibleRows.findIndex((row) => row.id === activeCell.rowId);
      const columnIndex = PASTE_FIELDS.indexOf(activeCell.field);
      if (rowIndex < 0 || columnIndex < 0) return;
      const nextRowIndex = clamp(rowIndex + rowDelta, 0, visibleRows.length - 1);
      const nextColumnIndex = clamp(columnIndex + columnDelta, 0, PASTE_FIELDS.length - 1);
      if (nextRowIndex === rowIndex && nextColumnIndex === columnIndex) return;
      const nextRow = visibleRows[nextRowIndex];
      const nextField = PASTE_FIELDS[nextColumnIndex];
      if (!nextRow || !nextField) return;
      setActiveCell({ rowId: nextRow.id, field: nextField });
    },
    [activeCell, visibleRows],
  );

  function handleCellKeyDown(event: React.KeyboardEvent<HTMLDivElement>): void {
    if (!activeCell) return;
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        moveActiveCell(1, 0);
        return;
      case 'ArrowUp':
        event.preventDefault();
        moveActiveCell(-1, 0);
        return;
      case 'ArrowRight':
        event.preventDefault();
        moveActiveCell(0, 1);
        return;
      case 'ArrowLeft':
        event.preventDefault();
        moveActiveCell(0, -1);
        return;
      case 'Home': {
        event.preventDefault();
        const firstField = PASTE_FIELDS[0];
        if (!firstField) return;
        if (event.ctrlKey) {
          // Ctrl+Home → grid 의 첫 cell
          const firstRow = visibleRows[0];
          if (!firstRow) return;
          setActiveCell({ rowId: firstRow.id, field: firstField });
        } else {
          setActiveCell({ rowId: activeCell.rowId, field: firstField });
        }
        return;
      }
      case 'End': {
        event.preventDefault();
        const lastField = PASTE_FIELDS[PASTE_FIELDS.length - 1];
        if (!lastField) return;
        if (event.ctrlKey) {
          const lastRow = visibleRows[visibleRows.length - 1];
          if (!lastRow) return;
          setActiveCell({ rowId: lastRow.id, field: lastField });
        } else {
          setActiveCell({ rowId: activeCell.rowId, field: lastField });
        }
        return;
      }
      case 'Enter':
      case 'F2':
        event.preventDefault();
        setIsEditing(true);
        return;
      default:
        // 사용자가 활성 cell 에서 일반 문자 키를 누르면 edit mode 진입.
        // ARIA APG grid pattern 의 "type to enter edit" 동작.
        if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
          setIsEditing(true);
        }
    }
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>): void {
    switch (event.key) {
      case 'Escape':
      case 'Enter':
        event.preventDefault();
        setIsEditing(false);
        // useEffect 가 cell focus 를 복원하므로 명시 focus 불필요.
        return;
      case 'Tab':
        // Tab/Shift+Tab → 다음 editable cell. 활성 cell 만 tabIndex=0 라 native
        // tab order 가 안 동작하므로 명시 처리.
        event.preventDefault();
        setIsEditing(false);
        moveActiveCell(0, event.shiftKey ? -1 : 1);
        return;
      default:
        return;
    }
  }

  // edit mode 진입 시 input 으로 focus 이동.
  useEffect(() => {
    if (!isEditing) return;
    const node = inputRef.current;
    if (node) node.focus({ preventScroll: true });
  }, [isEditing, activeCell]);

  return (
    <section className="grid-poc" aria-labelledby="grid-poc-heading">
      <PageHeader
        title="Phase 3 Grid PoC"
        titleId="grid-poc-heading"
        description="TanStack Table + Virtual prototype for ADR-0008 evidence. Dev gated."
      />

      <Toolbar ariaLabel="Technology draft">
        {TECHNOLOGIES.map((item) => (
          <Button
            key={item}
            type="button"
            variant={item === technology ? 'primary' : 'secondary'}
            className={
              item === technology ? 'grid-poc__button grid-poc__button--active' : 'grid-poc__button'
            }
            onClick={() => setTechnology(item)}
          >
            {item}
          </Button>
        ))}
      </Toolbar>

      <SectionBand title="Editable validation grid" meta={`${visibleRows.length} rows`} />
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <div
          ref={gridRef}
          className="grid-poc__viewport"
          role="grid"
          aria-rowcount={tableRows.length}
          aria-colcount={columns.length}
          onPaste={handlePaste}
        >
          <div className="grid-poc__header" role="row">
            {table.getFlatHeaders().map((header) => (
              <div
                key={header.id}
                className="grid-poc__cell grid-poc__cell--header"
                role="columnheader"
              >
                {flexRender(header.column.columnDef.header, header.getContext())}
              </div>
            ))}
          </div>
          <SortableContext
            items={visibleRows.map((row) => row.id)}
            strategy={verticalListSortingStrategy}
          >
            <div className="grid-poc__body" style={{ height: `${virtualizer.getTotalSize()}px` }}>
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const row = tableRows[virtualRow.index];
                if (!row) return null;
                return (
                  <SortableGridRow
                    key={row.id}
                    rowId={row.id}
                    virtualStart={virtualRow.start}
                    rowIndex={virtualRow.index + 2}
                  >
                    {row.getVisibleCells().map((cell, columnIndex) => {
                      const fieldId = cell.column.id;
                      const editableField: EditableKey | null = isEditableKey(fieldId)
                        ? fieldId
                        : null;
                      const isActiveCell =
                        editableField !== null &&
                        activeCell?.rowId === row.original.id &&
                        activeCell.field === editableField;
                      const inEditMode = isActiveCell && isEditing;
                      const headerLabel =
                        editableField !== null
                          ? (EDITABLE_HEADER_BY_KEY.get(editableField) ?? fieldId)
                          : fieldId;
                      return (
                        <div
                          key={cell.id}
                          ref={isActiveCell ? activeCellRef : undefined}
                          className={
                            isActiveCell
                              ? 'grid-poc__cell grid-poc__cell--active'
                              : 'grid-poc__cell'
                          }
                          role="gridcell"
                          aria-colindex={columnIndex + 1}
                          aria-selected={isActiveCell ? true : undefined}
                          tabIndex={editableField !== null && isActiveCell ? 0 : -1}
                          onFocus={() => {
                            if (editableField !== null && !isActiveCell) {
                              setActiveCell({ rowId: row.original.id, field: editableField });
                              setIsEditing(false);
                            }
                          }}
                          onClick={() => {
                            if (editableField !== null) {
                              setActiveCell({ rowId: row.original.id, field: editableField });
                              setIsEditing(false);
                            }
                          }}
                          onDoubleClick={() => {
                            if (editableField !== null) {
                              setActiveCell({ rowId: row.original.id, field: editableField });
                              setIsEditing(true);
                            }
                          }}
                          onKeyDown={(event) => {
                            if (editableField === null || !isActiveCell) return;
                            handleCellKeyDown(event);
                          }}
                        >
                          {inEditMode && editableField !== null ? (
                            <input
                              ref={inputRef}
                              aria-label={`${headerLabel} for row ${describeRowForA11y(row.original)}`}
                              value={String(row.original[editableField] ?? '')}
                              onChange={(event) =>
                                updateCell(row.original.id, editableField, event.target.value)
                              }
                              onBlur={() => setIsEditing(false)}
                              onKeyDown={handleInputKeyDown}
                            />
                          ) : editableField !== null ? (
                            <span
                              aria-label={`${headerLabel} for row ${describeRowForA11y(row.original)}`}
                            >
                              {String(row.original[editableField] ?? '')}
                            </span>
                          ) : (
                            flexRender(cell.column.columnDef.cell, cell.getContext())
                          )}
                        </div>
                      );
                    })}
                  </SortableGridRow>
                );
              })}
            </div>
          </SortableContext>
        </div>
      </DndContext>

      <SectionBand title="Draft change log" />
      <ol className="grid-poc__changes" aria-live="polite">
        {changes.length === 0 ? (
          <li className="grid-poc__muted">No edits yet.</li>
        ) : (
          changes.map((change, index) => (
            <li key={`${change.rowId}-${change.field}-${index}`}>
              <span className="grid-poc__mono">{change.rowId}</span> {change.field}:{' '}
              <span className="grid-poc__muted">{change.before || 'blank'}</span> -&gt;{' '}
              <strong>{change.after || 'blank'}</strong>
            </li>
          ))
        )}
      </ol>
    </section>
  );
}

interface SortableGridRowProps {
  readonly rowId: string;
  readonly virtualStart: number;
  readonly rowIndex: number;
  readonly children: ReactNode;
}

function SortableGridRow({
  rowId,
  virtualStart,
  rowIndex,
  children,
}: SortableGridRowProps): JSX.Element {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: rowId,
  });
  const dragTransform = CSS.Transform.toString(transform);
  const combinedTransform = dragTransform
    ? `translateY(${virtualStart}px) ${dragTransform}`
    : `translateY(${virtualStart}px)`;
  return (
    <div
      ref={setNodeRef}
      className={isDragging ? 'grid-poc__row grid-poc__row--dragging' : 'grid-poc__row'}
      style={{ transform: combinedTransform, transition }}
      {...attributes}
      role="row"
      aria-rowindex={rowIndex}
      {...listeners}
    >
      {children}
    </div>
  );
}

function editableColumn(key: EditableKey, kind: 'text' | 'number' = 'text'): ColumnDef<GridPocRow> {
  const header = EDITABLE_HEADER_BY_KEY.get(key) ?? key;
  return {
    accessorKey: key,
    header,
    cell: (ctx) => (
      <span className={kind === 'number' ? 'grid-poc__number' : undefined}>
        {String(ctx.getValue<string | number | null>() ?? '')}
      </span>
    ),
  };
}

function parseEditableValue(field: EditableKey, raw: string): string | number | null {
  const trimmed = raw.trim();
  if (trimmed === '') return null;
  if (field === 'targetPowerDbm' || field === 'limitDbm') {
    const value = Number(trimmed.replace(',', '.'));
    return Number.isFinite(value) ? value : null;
  }
  if (field === 'sampleCount') {
    const value = Number.parseInt(trimmed, 10);
    return Number.isFinite(value) ? value : null;
  }
  return raw;
}
