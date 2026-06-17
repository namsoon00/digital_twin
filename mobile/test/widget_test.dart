import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:market_flow/main.dart';
import 'package:market_flow/src/data/economic_feed_service.dart';
import 'package:market_flow/src/data/flow_repository.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<void> pumpMarketFlowApp(WidgetTester tester) async {
  const repository = MockFlowRepository();
  await tester.pumpWidget(
    MarketFlowApp(
      repository: repository,
      economicFeedService: StaticEconomicFeedService(repository.economicFeeds),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('Google News RSS items are mapped into economic feed items', () async {
    const rss = '''
<rss><channel>
  <item>
    <title><![CDATA[AI CAPEX expands into power infrastructure]]></title>
    <link>https://articles.example.com/ai-power</link>
    <pubDate>Tue, 16 Jun 2026 03:24:00 GMT</pubDate>
    <source url="https://reuters.com">Reuters</source>
    <description><![CDATA[Cloud spending and grid equipment orders are rising across the AI buildout.]]></description>
  </item>
</channel></rss>
''';
    final service = GoogleNewsEconomicFeedService(
      client: MockClient((request) async {
        expect(request.url.host, 'news.google.com');
        return http.Response(
          rss,
          200,
          headers: {'content-type': 'application/rss+xml; charset=utf-8'},
        );
      }),
    );
    addTearDown(service.dispose);

    final result = await service.fetchFeeds();

    expect(result.snapshot.status, EconomicFeedFetchStatus.ready);
    expect(result.items, hasLength(1));
    expect(
      result.items.single.title,
      'AI CAPEX expands into power infrastructure',
    );
    expect(result.items.single.source, 'Reuters');
    expect(result.items.single.url, 'https://articles.example.com/ai-power');
    expect(result.items.single.summary, contains('Cloud spending'));
  });

  test(
    'Google News RSS uses local proxy fallback after direct fetch failure',
    () async {
      const rss = '''
<rss><channel>
  <item>
    <title>Fed liquidity watch lifts stock futures</title>
    <link>https://articles.example.com/fed-liquidity</link>
    <pubDate>Tue, 16 Jun 2026 04:24:00 GMT</pubDate>
    <source>MarketWatch</source>
    <description>Dollar funding and rate expectations are moving equity futures.</description>
  </item>
</channel></rss>
''';
      var proxyRequestCount = 0;
      final service = GoogleNewsEconomicFeedService(
        client: MockClient((request) async {
          if (request.url.host == 'news.google.com') {
            throw http.ClientException('Failed to fetch', request.url);
          }
          expect(request.url.host, '127.0.0.1');
          expect(request.url.port, 3000);
          expect(request.url.path, '/api/economic-feed/rss');
          expect(
            request.url.queryParameters['url'],
            contains('https://news.google.com/rss/search'),
          );
          proxyRequestCount += 1;
          return http.Response(
            rss,
            200,
            headers: {'content-type': 'application/rss+xml; charset=utf-8'},
          );
        }),
      );
      addTearDown(service.dispose);

      final result = await service.fetchFeeds();

      expect(result.snapshot.status, EconomicFeedFetchStatus.ready);
      expect(result.items, hasLength(1));
      expect(result.items.single.source, 'MarketWatch');
      expect(proxyRequestCount, 7);
    },
  );

  testWidgets('MarketFlow defaults to dark mode', (tester) async {
    await pumpMarketFlowApp(tester);

    final app = tester.widget<MaterialApp>(find.byType(MaterialApp));

    expect(app.themeMode, ThemeMode.dark);
    expect(app.darkTheme?.brightness, Brightness.dark);
    expect(app.darkTheme?.scaffoldBackgroundColor, const Color(0xFF0B1017));
  });

  testWidgets('MarketFlow changes theme mode from settings', (tester) async {
    await pumpMarketFlowApp(tester);

    await tester.tap(find.text('설정'));
    await tester.pumpAndSettle();

    expect(find.text('화면 테마'), findsOneWidget);
    expect(find.text('다크'), findsOneWidget);
    expect(find.text('라이트'), findsOneWidget);

    await tester.tap(find.text('라이트'));
    await tester.pumpAndSettle();

    var app = tester.widget<MaterialApp>(find.byType(MaterialApp));
    expect(app.themeMode, ThemeMode.light);
    expect(app.theme?.scaffoldBackgroundColor, const Color(0xFFF4F6F8));

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('app.themeMode'), 'light');

    await tester.tap(find.text('다크'));
    await tester.pumpAndSettle();

    app = tester.widget<MaterialApp>(find.byType(MaterialApp));
    expect(app.themeMode, ThemeMode.dark);
    expect(app.darkTheme?.scaffoldBackgroundColor, const Color(0xFF0B1017));
    expect(prefs.getString('app.themeMode'), 'dark');
  });

  testWidgets('MarketFlow opens on the live flow dashboard', (tester) async {
    await pumpMarketFlowApp(tester);

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
    await pumpMarketFlowApp(tester);

    await tester.tap(find.text('기록'));
    await tester.pumpAndSettle();

    expect(find.text('감 기록'), findsOneWidget);
    expect(find.text('NVDA'), findsOneWidget);
  });

  testWidgets('MarketFlow shows the economic feed tab', (tester) async {
    await pumpMarketFlowApp(tester);

    await tester.tap(find.text('피드'));
    await tester.pumpAndSettle();

    expect(find.text('경제가 돌아가는 방향'), findsOneWidget);
    expect(find.text('AI CAPEX 자금이 반도체에서 전력 인프라로 확산'), findsWidgets);

    await tester.scrollUntilVisible(
      find.text('경제 피드'),
      260,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('경제 피드'), findsOneWidget);
    expect(find.text('AI CAPEX 자금이 반도체에서 전력 인프라로 확산'), findsWidgets);
    expect(find.text('MarketFlow Theme Map · 18분 전'), findsOneWidget);
  });

  testWidgets('MarketFlow manages the pre-investment checklist', (
    tester,
  ) async {
    await pumpMarketFlowApp(tester);

    await tester.tap(find.text('체크'));
    await tester.pumpAndSettle();

    expect(find.text('오늘 데이터 확인'), findsOneWidget);
    expect(find.text('시장 펄스'), findsOneWidget);
    expect(find.text('자금 흐름'), findsOneWidget);
    expect(find.text('관심 종목 신호'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('체크 캘린더'),
      320,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('체크 캘린더'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('오늘 투자 전 체크'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('오늘 투자 전 체크'), findsOneWidget);
    expect(find.text('글로벌 지수와 환율 방향 확인'), findsOneWidget);
    expect(find.text('선택일 초기화'), findsOneWidget);
    expect(find.text('0/7 완료'), findsOneWidget);

    await tester.tap(find.byType(Checkbox).first);
    await tester.pumpAndSettle();

    expect(find.text('1/7 완료'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('체크 항목 추가'),
      260,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '현금 비중 확인');
    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();

    expect(find.text('현금 비중 확인'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('메모 저장'),
      260,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).last, 'FOMC 전까지 절반만');
    await tester.tap(find.text('메모 저장'));
    await tester.pumpAndSettle();

    expect(find.text('체크 메모 저장됨'), findsOneWidget);
  });

  testWidgets('MarketFlow shows global capital flows', (tester) async {
    await pumpMarketFlowApp(tester);

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
    await pumpMarketFlowApp(tester);

    await tester.tap(find.text('설정'));
    await tester.pumpAndSettle();

    expect(find.text('화면 테마'), findsOneWidget);
    expect(find.text('기기 로컬 설정에 저장'), findsOneWidget);
    expect(find.text('시스템'), findsOneWidget);
    expect(find.text('라이트'), findsOneWidget);
    expect(find.text('다크'), findsOneWidget);
    expect(find.text('데이터 API key'), findsOneWidget);
    expect(find.text('기본 데이터 조회용 key를 기기 로컬 설정에 저장'), findsOneWidget);
    expect(find.text('Alpha Vantage'), findsOneWidget);
    expect(find.text('FRED API'), findsOneWidget);
    expect(find.text('CoinGecko API'), findsOneWidget);
    expect(find.text('DefiLlama API'), findsOneWidget);
    expect(
      find.text('발급 위치: https://www.alphavantage.co/documentation/'),
      findsOneWidget,
    );
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
