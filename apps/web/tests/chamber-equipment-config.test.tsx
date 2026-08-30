import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { EquipmentConfigPanel } from '@/routes/chambers/EquipmentConfigPanel';

import type { ReactElement } from 'react';

/**
 * SPLIT-6 ③ — 계측기 연결 설정 화면.
 *
 * 이 파일이 지키는 성질은 넷이고, 셋은 정확성 요구사항이지 미학이 아니다:
 *
 * 1. **입력 칸이 descriptor 에서만 만들어진다.** 여기에도 화면에도 필드 이름이 한 개도
 *    적혀 있지 않아야 한다 — 두 번째 provider 가 자기 레포에서 descriptor 만 바꿔도
 *    이 화면이 그것을 렌더해야 하기 때문이다(ADR-0018 D-6).
 * 2. **저장이 더러운 키만 보낸다.** 서버는 키 단위로 병합하고 그것이 이 설계의 동시성
 *    보호 전부다. 렌더된 칸을 전부 보내면 전량 교체로 되돌아가, 내가 만지지도 않은 칸의
 *    옛 값이 남의 저장을 덮는다.
 * 3. **빈 칸은 삭제이지 빈 문자열이 아니다.** ''를 저장하면 노드는 '설정됐는데 비어 있다'로
 *    읽고 워크북 폴백을 하지 않는다.
 * 4. **저장이 비낙관적이다.** 저장 결과는 이 패치와 저장돼 있던 것의 병합이고, 그것을
 *    브라우저에서 계산하면 서버 병합 규칙의 두 번째 정의가 생긴다.
 */

const platformApi = vi.hoisted(() => ({
  fetchProviderUiDescriptor: vi.fn(),
  fetchChamberEquipmentConfig: vi.fn(),
  updateChamberEquipmentConfig: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

/** Field ids/labels a provider might ship. Invented here on purpose — if the
 *  panel only renders what it is handed, the test can hand it anything. */
const DESCRIPTOR = {
  equipment: [
    {
      group_id: 'group-a',
      label: 'Device Info',
      sheet_name: null,
      fields: [
        { field_id: 'Alpha Addr:', label: 'Alpha Addr:', data_type: 'ipv4', required: false },
        { field_id: 'Beta Bus:', label: 'Beta Bus:', data_type: 'gpib_address', required: false },
        { field_id: 'Gamma Note:', label: 'Gamma Note:', data_type: 'string', required: false },
      ],
    },
  ],
};

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

function authenticateAs(permissions: readonly string[]): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: 'op@corp', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

const CHAMBERS = [
  {
    chamber_id: 'cham-1',
    name: 'Chamber 1',
    base_url: 'http://node-1:8000',
    enabled: true,
    status: 'idle',
    heartbeat_ttl_seconds: 30,
  },
] as never;

function renderPanel(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/chambers?chamber=cham-1']}>
        <EquipmentConfigPanel chambers={CHAMBERS} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

describe('EquipmentConfigPanel', () => {
  beforeEach(() => {
    __resetAuthStateForTests();
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(DESCRIPTOR);
    platformApi.fetchChamberEquipmentConfig.mockResolvedValue({
      chamber_id: 'cham-1',
      equipment_config: { 'Alpha Addr:': '10.0.0.1', 'Beta Bus:': 'GPIB0::18' },
      updated_at: '2026-08-10T00:00:00+00:00',
    });
    platformApi.updateChamberEquipmentConfig.mockResolvedValue({
      chamber_id: 'cham-1',
      equipment_config: {},
      updated_at: '2026-08-10T00:01:00+00:00',
    });
    authenticateAs(['platform:read', 'platform:chamber-config-write']);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders one input per descriptor field and nothing it was not given', async () => {
    renderPanel();
    const inputs = await screen.findAllByTestId('chambers-equipment-input');
    const declared = DESCRIPTOR.equipment.flatMap((group) => group.fields);
    expect(inputs).toHaveLength(declared.length);
    expect(inputs.map((input) => input.getAttribute('data-field-id'))).toEqual(
      declared.map((field) => field.field_id),
    );
  });

  it('shows the stored value for a field the server has, and blank for one it does not', async () => {
    renderPanel();
    const inputs = await screen.findAllByTestId('chambers-equipment-input');
    expect((inputs[0] as HTMLInputElement).value).toBe('10.0.0.1');
    expect((inputs[2] as HTMLInputElement).value).toBe('');
  });

  it('sends ONLY the edited keys', async () => {
    renderPanel();
    const inputs = await screen.findAllByTestId('chambers-equipment-input');
    await userEvent.clear(inputs[1] as HTMLInputElement);
    await userEvent.type(inputs[1] as HTMLInputElement, 'GPIB0::20');
    await userEvent.click(screen.getByTestId('chambers-equipment-save'));

    await waitFor(() => expect(platformApi.updateChamberEquipmentConfig).toHaveBeenCalled());
    const [chamberId, body] = platformApi.updateChamberEquipmentConfig.mock.calls[0] as [
      string,
      { equipment_config: Record<string, string | null> },
    ];
    expect(chamberId).toBe('cham-1');
    // The untouched key must be ABSENT, not echoed — echoing it is how one
    // browser's stale copy silently overwrites another operator's save.
    expect(Object.keys(body.equipment_config)).toEqual(['Beta Bus:']);
    expect(body.equipment_config['Beta Bus:']).toBe('GPIB0::20');
  });

  it('sends null (delete), not an empty string, for a cleared field', async () => {
    renderPanel();
    const inputs = await screen.findAllByTestId('chambers-equipment-input');
    await userEvent.clear(inputs[0] as HTMLInputElement);
    await userEvent.click(screen.getByTestId('chambers-equipment-save'));

    await waitFor(() => expect(platformApi.updateChamberEquipmentConfig).toHaveBeenCalled());
    const [, body] = platformApi.updateChamberEquipmentConfig.mock.calls[0] as [
      string,
      { equipment_config: Record<string, string | null> },
    ];
    expect(body.equipment_config).toEqual({ 'Alpha Addr:': null });
  });

  it('re-reads from the server after a save instead of computing the result', async () => {
    renderPanel();
    const inputs = await screen.findAllByTestId('chambers-equipment-input');
    await userEvent.type(inputs[2] as HTMLInputElement, 'x');
    await userEvent.click(screen.getByTestId('chambers-equipment-save'));

    await waitFor(() =>
      expect(platformApi.fetchChamberEquipmentConfig.mock.calls.length).toBeGreaterThan(1),
    );
    await screen.findByTestId('chambers-equipment-success');
  });

  it('does not offer to save until something is edited', async () => {
    renderPanel();
    await screen.findAllByTestId('chambers-equipment-input');
    expect(screen.getByTestId('chambers-equipment-save')).toBeDisabled();
  });

  it('hides the save affordance without the write permission', async () => {
    authenticateAs(['platform:read']);
    renderPanel();
    const inputs = await screen.findAllByTestId('chambers-equipment-input');
    expect(inputs[0]).toHaveAttribute('readonly');
    expect(screen.queryByTestId('chambers-equipment-save')).toBeNull();
  });

  it('asks for a chamber before reading anything', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/chambers']}>
          <EquipmentConfigPanel chambers={CHAMBERS} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByTestId('chambers-equipment-unselected');
    expect(platformApi.fetchChamberEquipmentConfig).not.toHaveBeenCalled();
  });

  it('surfaces a failed save instead of reporting success', async () => {
    platformApi.updateChamberEquipmentConfig.mockRejectedValue(new Error('nope'));
    renderPanel();
    const inputs = await screen.findAllByTestId('chambers-equipment-input');
    await userEvent.type(inputs[2] as HTMLInputElement, 'x');
    await userEvent.click(screen.getByTestId('chambers-equipment-save'));

    await screen.findByTestId('chambers-equipment-save-error');
    expect(screen.queryByTestId('chambers-equipment-success')).toBeNull();
  });
});
