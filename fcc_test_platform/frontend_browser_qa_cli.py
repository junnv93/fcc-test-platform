"""Collect platform frontend browser QA evidence with Selenium.

This runtime collector is intentionally separate from
platform_frontend_qa_evidence.py so the production cutover validator remains
dependency-free.

⚠️ 이것은 `scripts/platform_frontend_browser_qa.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


from fcc_test_platform.frontend_qa_evidence import (  # noqa: E402
    FRONTEND_REQUIRED_VIEWS,
    frontend_qa_errors,
)


DEFAULT_VIEWPORTS = (
    'desktop=1440x900',
    'tablet=900x900',
    'mobile=390x844',
)
PROVIDER_PROBE_ROUTES = (
    '/health',
    '/headless/api-contract',
    '/headless/capabilities',
)


@dataclass(frozen=True)
class Viewport:
    name: str
    width: int
    height: int


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Collect frontend browser QA evidence.')
    parser.add_argument('--app-url', required=True)
    parser.add_argument('--provider-api-url', required=True)
    parser.add_argument('--evidence-id', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--artifact-root', default='artifacts')
    parser.add_argument('--screenshot-dir', default='artifacts/frontend/browser-qa')
    parser.add_argument('--browser', choices=('chrome', 'edge'), default='chrome')
    parser.add_argument('--bearer-token', default='')
    parser.add_argument('--auth-flow-verified', action='store_true')
    parser.add_argument('--route-timeout-seconds', type=float, default=5.0)
    parser.add_argument('--viewport', action='append', default=[])
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    try:
        viewports = [_parse_viewport(value) for value in (args.viewport or list(DEFAULT_VIEWPORTS))]
        driver = _create_driver(args.browser)
        try:
            manifest = collect_manifest(
                driver=driver,
                app_url=args.app_url,
                provider_api_url=args.provider_api_url,
                evidence_id=args.evidence_id,
                artifact_root=Path(args.artifact_root),
                screenshot_dir=Path(args.screenshot_dir),
                auth_flow_verified=args.auth_flow_verified,
                bearer_token=args.bearer_token,
                viewports=viewports,
                route_timeout_seconds=args.route_timeout_seconds,
            )
        finally:
            driver.quit()
    except Exception as exc:
        print(json.dumps({'collected': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')

    issues = [issue.to_dict() for issue in frontend_qa_errors(manifest)]
    print(json.dumps({'collected': True, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0


def collect_manifest(
    *,
    driver,
    app_url: str,
    provider_api_url: str,
    evidence_id: str,
    artifact_root: Path,
    screenshot_dir: Path,
    auth_flow_verified: bool,
    bearer_token: str,
    viewports: list[Viewport],
    route_timeout_seconds: float,
) -> dict:
    route_failures = _probe_provider_routes(provider_api_url, route_timeout_seconds, bearer_token=bearer_token)
    screenshot_base = screenshot_dir / evidence_id
    screenshot_base.mkdir(parents=True, exist_ok=True)

    viewport_results = []
    screenshots = []
    for viewport in viewports:
        result, screenshot = _collect_viewport(
            driver=driver,
            app_url=app_url,
            viewport=viewport,
            artifact_root=artifact_root,
            screenshot_base=screenshot_base,
            bearer_token=bearer_token,
            route_failures=route_failures,
        )
        viewport_results.append(result)
        screenshots.append(screenshot)

    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'app_url': app_url,
        'browser': _browser_label(driver),
        'auth_flow_verified': bool(auth_flow_verified or (bearer_token and not route_failures)),
        'central_backend_verified': not route_failures,
        'provider_contract_routes_verified': not route_failures,
        'viewport_results': viewport_results,
        'screenshots': screenshots,
    }


def _collect_viewport(
    *,
    driver,
    app_url: str,
    viewport: Viewport,
    artifact_root: Path,
    screenshot_base: Path,
    bearer_token: str,
    route_failures: list[str],
) -> tuple[dict, dict]:
    driver.set_window_size(viewport.width, viewport.height)
    driver.get(app_url)
    if bearer_token:
        _seed_bearer_token(driver, bearer_token)
        driver.get(app_url)
    views_verified = _verify_required_views(driver)
    console_errors = _browser_console_errors(driver)
    failed_requests = list(route_failures)
    screenshot_path = screenshot_base / f'{viewport.name}.png'
    driver.save_screenshot(str(screenshot_path))

    rendered = _body_rendered(driver)
    responsive_pass = _responsive_pass(driver)
    result = {
        'name': viewport.name,
        'width': viewport.width,
        'height': viewport.height,
        'rendered': rendered,
        'responsive_pass': responsive_pass,
        'views_verified': views_verified,
        'console_errors': console_errors,
        'failed_requests': failed_requests,
    }
    screenshot = {
        'viewport': viewport.name,
        'relative_path': _relative_artifact_path(screenshot_path, artifact_root),
        'sha256': _sha256_file(screenshot_path),
    }
    return result, screenshot


def _verify_required_views(driver) -> list[str]:
    verified = []
    for view in FRONTEND_REQUIRED_VIEWS:
        try:
            driver.find_element('css selector', f'[data-view="{view}"]').click()
            active = driver.find_element('css selector', f'#{view}.view.active')
        except Exception:
            continue
        if getattr(active, 'is_displayed', lambda: True)():
            verified.append(view)
    return verified


def _body_rendered(driver) -> bool:
    try:
        body = driver.find_element('css selector', 'body')
    except Exception:
        return False
    return getattr(body, 'is_displayed', lambda: True)()


def _responsive_pass(driver) -> bool:
    try:
        return bool(driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth + 2'))
    except Exception:
        return False


def _browser_console_errors(driver) -> list[str]:
    try:
        logs = driver.get_log('browser')
    except Exception:
        return []
    errors = []
    for entry in logs:
        level = str(entry.get('level', '')).upper()
        message = str(entry.get('message', ''))
        if level in {'SEVERE', 'ERROR'}:
            errors.append(message)
    return errors


def _seed_bearer_token(driver, bearer_token: str) -> None:
    driver.execute_script(
        "sessionStorage.setItem('fcc-platform-shell-bearer-token', arguments[0]);",
        bearer_token,
    )


def _probe_provider_routes(provider_api_url: str, timeout_seconds: float, *, bearer_token: str = '') -> list[str]:
    failures = []
    for route in PROVIDER_PROBE_ROUTES:
        url = urljoin(provider_api_url.rstrip('/') + '/', route.lstrip('/'))
        headers = {'Accept': 'application/json'}
        if bearer_token:
            headers['Authorization'] = f'Bearer {bearer_token}'
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                if response.status >= 400:
                    failures.append(f'{route}: HTTP {response.status}')
        except HTTPError as exc:
            failures.append(f'{route}: HTTP {exc.code}')
        except URLError as exc:
            failures.append(f'{route}: {exc.reason}')
    return failures


def _browser_label(driver) -> str:
    capabilities = getattr(driver, 'capabilities', {}) or {}
    name = capabilities.get('browserName') or 'browser'
    version = capabilities.get('browserVersion') or capabilities.get('version') or 'unknown'
    return f'{name} {version}'


def _relative_artifact_path(path: Path, artifact_root: Path) -> str:
    try:
        return path.resolve().relative_to(artifact_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_viewport(value: str) -> Viewport:
    name, _, size = value.partition('=')
    width_text, _, height_text = size.partition('x')
    if not name or not width_text or not height_text:
        raise ValueError('viewport must use name=WIDTHxHEIGHT')
    width = int(width_text)
    height = int(height_text)
    if width <= 0 or height <= 0:
        raise ValueError('viewport width and height must be positive')
    return Viewport(name=name, width=width, height=height)


def _create_driver(browser: str):
    if browser == 'edge':
        from selenium.webdriver import Edge

        return Edge()
    from selenium.webdriver import Chrome

    return Chrome()


if __name__ == '__main__':
    raise SystemExit(main())
