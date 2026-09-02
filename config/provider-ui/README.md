# provider UI descriptor 를 놓는 자리

platform-api 가 기동할 때 이 디렉터리의 `*.json` 을 읽어
`ProviderUiDescriptorRegistry` 에 배선한다. 판정 코드는
`fcc_test_platform/application/provider_ui_descriptor_loader.py` 다.

## ⚠️ 이 저장소는 descriptor 를 담지 않는다

`*.json` 은 **gitignore 대상**이다. descriptor 는 provider 소유 내용이고, 여기 담으면
provider 저장소의 것과 **두 벌**이 된다. 사본은 갈라진다 — 2026-09-01 실측: `web` 을
분리하면서 남긴 사본과 원본이 갈라져 있었고(게이트웨이 업로드 천장이 한쪽에만
고쳐졌다) 그것이 사본을 지운 이유다.

## 놓는 방법

provider 배포가 자기 descriptor 를 JSON 으로 내보내 이 디렉터리에 놓는다.
파일 이름은 자유다 — **registry 키는 파일 안의 `provider_id`** 다. 이름을 키로 쓰면
이름과 내용이 두 번째 SSOT 가 되어, 잘못 이름 붙인 배포가 다른 provider 의 화면을 그린다.

`fcc-unlicensed-conducted` 의 경우 (챔버 PC 에서):

```bash
python -c "
import json
from application.headless.provider_ui_descriptor import build_unlicensed_ui_descriptor
print(json.dumps(build_unlicensed_ui_descriptor().to_dict(), ensure_ascii=False, indent=2))
" > fcc-unlicensed-conducted.json
```

그 파일을 중앙 PC 의 이 디렉터리로 옮긴다. compose 가 이것을 컨테이너의
`/app/config/provider-ui` 로 마운트한다(`FCC_PLATFORM_PROVIDER_UI_DIR` 로 바꿀 수 있다).

## 비어 있으면

**정상이다** — 아직 아무 provider 도 배포되지 않은 상태다. 다만 「배포했는데 경로가
틀렸다」와 모양이 같으므로, platform-api 가 **기동 로그로 몇 개를 어디서 읽었는지
말한다.** 화면의 provider 목록이 비었으면 그 로그를 먼저 보라.

깨진 JSON · `provider_id` 없음 · 같은 `provider_id` 두 파일은 **기동을 거부한다**.
없는 것과 깨진 것은 다른 사실이고, 깨진 것을 「provider 없음」으로 접으면 화면이 `[]`
을 보여주어 운영자가 배포를 다시 하게 만든다.
