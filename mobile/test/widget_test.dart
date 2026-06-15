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

  testWidgets('MarketFlow shows Toss Securities settings', (tester) async {
    await tester.pumpWidget(const MarketFlowApp());
    await tester.pumpAndSettle();

    await tester.tap(find.text('설정'));
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
