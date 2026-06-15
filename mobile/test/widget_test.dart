import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:market_flow/main.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('MarketFlow opens on the live flow dashboard', (tester) async {
    await tester.pumpWidget(const MarketFlowApp());
    await tester.pumpAndSettle();

    expect(find.text('MarketFlow'), findsOneWidget);
    expect(find.text('API key 필요'), findsWidgets);
    expect(find.text('Alpha Vantage GLOBAL_QUOTE'), findsOneWidget);
    expect(find.text('시장 펄스'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('테마 확산'),
      280,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('테마 확산'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('오늘의 신호'),
      320,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('오늘의 신호'), findsOneWidget);
  });

  testWidgets('MarketFlow switches to the journal tab', (tester) async {
    await tester.pumpWidget(const MarketFlowApp());

    await tester.tap(find.text('기록'));
    await tester.pumpAndSettle();

    expect(find.text('감 기록'), findsOneWidget);
    expect(find.text('NVDA'), findsOneWidget);
  });

  testWidgets('MarketFlow shows global capital flows', (tester) async {
    await tester.pumpWidget(const MarketFlowApp());
    await tester.pumpAndSettle();

    await tester.tap(find.text('자금'));
    await tester.pumpAndSettle();

    expect(find.text('종합 플로우 캔들'), findsOneWidget);
    expect(find.text('1M'), findsOneWidget);
    expect(find.text('3M'), findsOneWidget);
    expect(find.text('ALL'), findsOneWidget);
    expect(find.text('캔들: 종합지수'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('필요 API 맵'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('필요 API 맵'), findsOneWidget);
    expect(find.text('Alpha Vantage'), findsOneWidget);
    expect(find.text('토스증권 Open API'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('FRED API'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('FRED API'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('CoinGecko API'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('CoinGecko API'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('ETF/Fund Flow API'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('ETF/Fund Flow API'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('세계 자금 흐름'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('AI 인프라 CAPEX'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('디지털 자산 베타'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('디지털 자산 베타'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('금/안전자산 축적'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('금/안전자산 축적'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('새 흐름 후보'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('새 흐름 후보'), findsOneWidget);
    expect(find.text('미국 개인의 KOSPI 직접 접근 확대'), findsOneWidget);
  });

  testWidgets('MarketFlow shows Toss Securities settings', (tester) async {
    await tester.pumpWidget(const MarketFlowApp());
    await tester.pumpAndSettle();

    await tester.tap(find.text('설정'));
    await tester.pumpAndSettle();

    expect(find.text('데이터 API key'), findsOneWidget);
    expect(find.text('기본 데이터 조회용 key를 기기 로컬 설정에 저장'), findsOneWidget);
    expect(find.text('Alpha Vantage'), findsOneWidget);
    expect(find.text('FRED API'), findsOneWidget);
    expect(find.text('CoinGecko API'), findsOneWidget);
    expect(find.text('DefiLlama API'), findsOneWidget);
    expect(find.text('읽기 전용 데이터'), findsOneWidget);
    expect(find.text('API key'), findsWidgets);
    expect(find.text('데이터 API key 저장'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('토스증권 계정'),
      520,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('토스증권 계정'), findsOneWidget);
    expect(find.text('앱에서 Open API 직접 호출'), findsOneWidget);
    expect(find.text('Open API 기본 URL'), findsOneWidget);
    expect(find.text('앱 키'), findsOneWidget);
    expect(find.text('앱 시크릿'), findsOneWidget);
    expect(find.text('액세스 토큰'), findsOneWidget);
    expect(find.text('연결 테스트'), findsOneWidget);
    expect(find.text('주문 기능 잠금'), findsOneWidget);
  });
}
