# MarketFlow

MarketFlow는 한국/미국 주식의 현재 흐름을 테마, 종목, 감 기록으로 추적하는 Flutter 앱입니다.

## 실행

```bash
flutter run
```

실제 기기에 설치하려면 `mobile` 폴더에서 iOS 또는 Android 디바이스를 선택해 실행하세요.

## 검증

```bash
flutter analyze
flutter test
```

## 구조

- `lib/src/models`: 시장, 테마, 종목, 사용자, 기록 모델
- `lib/src/data`: 나중에 API/서버 저장소로 교체 가능한 repository 계층
- `lib/src/screens`: 하단 탭 기반 앱 화면
- `lib/src/widgets`: 카드, 차트, 시장 전환 등 재사용 UI
- `lib/src/theme`: 앱 색상과 Material 3 테마

## 현재 범위

현재 앱은 mock 데이터로 동작합니다. 다음 단계에서 실시간/지연 시세 API, 사용자 인증, 서버 저장소, 유료 플랜, 알림을 붙일 수 있도록 데이터 접근을 repository로 분리했습니다.
