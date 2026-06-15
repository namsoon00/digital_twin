import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:market_flow/main.dart';

void main() {
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
}
