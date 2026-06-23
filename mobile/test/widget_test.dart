import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:market_flow/main.dart';
import 'package:market_flow/src/data/crypto_market_service.dart';
import 'package:market_flow/src/data/data_api_probe_service.dart';
import 'package:market_flow/src/data/economic_feed_service.dart';
import 'package:market_flow/src/data/flow_repository.dart';
import 'package:market_flow/src/data/local_settings_database.dart';
import 'package:market_flow/src/data/settings_repository.dart';
import 'package:market_flow/src/models/market_models.dart';
import 'package:market_flow/src/widgets/sparkline.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<void> pumpMarketFlowApp(WidgetTester tester) async {
  const repository = MockFlowRepository();
  await tester.pumpWidget(
    MarketFlowApp(
      repository: repository,
      cryptoMarketService: StaticCryptoMarketService(repository.cryptoAssets),
      economicFeedService: StaticEconomicFeedService(repository.economicFeeds),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> dismissModal(WidgetTester tester, Finder anchor) async {
  Navigator.of(tester.element(anchor)).pop();
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
    expect(result.items.single.channelId, 'us-liquidity');
    expect(result.items.single.channelName, '미국 유동성');
    expect(result.items.single.summary, contains('Cloud spending'));
  });

  test('Google News RSS exposes configured feed channels', () {
    final channels = GoogleNewsEconomicFeedService.defaultFeedChannels;

    expect(channels, hasLength(7));
    expect(channels.first.name, '미국 유동성');
    expect(channels.first.provider, 'Google News RSS');
    expect(channels.first.query, contains('미국 금리'));
    expect(channels.first.url, startsWith('https://news.google.com/search'));
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

  test('Data API probe validates Alpha Vantage quote access', () async {
    const repository = MockFlowRepository();
    final source = repository.dataApiSources.firstWhere(
      (source) => source.id == 'alpha-vantage',
    );
    final client = DataApiProbeClient(
      client: MockClient((request) async {
        expect(request.url.host, 'www.alphavantage.co');
        expect(request.url.queryParameters['function'], 'GLOBAL_QUOTE');
        expect(request.url.queryParameters['symbol'], 'NVDA');
        expect(request.url.queryParameters['apikey'], 'demo-key');
        return http.Response(
          '''
{
  "Global Quote": {
    "01. symbol": "NVDA",
    "05. price": "142.1800",
    "06. volume": "42000000",
    "09. change": "3.40",
    "10. change percent": "2.45%"
  }
}
''',
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );
    addTearDown(client.dispose);

    final result = await client.probe(source, 'demo-key');

    expect(result.ok, isTrue);
    expect(result.endpoint, 'GLOBAL_QUOTE NVDA');
    expect(result.message, contains('142.1800'));
    expect(result.linkedDataLabel, contains('관심 종목'));
  });

  test('Data API probe retries FRED through the local proxy', () async {
    const repository = MockFlowRepository();
    final source = repository.dataApiSources.firstWhere(
      (source) => source.id == 'fred',
    );
    var requestCount = 0;
    final client = DataApiProbeClient(
      client: MockClient((request) async {
        requestCount += 1;
        if (requestCount == 1) {
          expect(request.url.host, 'api.stlouisfed.org');
          return http.Response('CORS blocked in browser', 500);
        }

        expect(request.url.host, '127.0.0.1');
        expect(request.url.path, '/api/data-api/fred/observations');
        expect(request.url.queryParameters['series_id'], 'DGS10');
        expect(request.url.queryParameters['api_key'], 'fred-key');
        return http.Response(
          '''
{
  "observations": [
    {"date": "2026-06-18", "value": "4.46"}
  ]
}
''',
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );
    addTearDown(client.dispose);

    final result = await client.probe(source, 'fred-key');

    expect(result.ok, isTrue);
    expect(result.endpoint, 'series/observations DGS10');
    expect(result.message, contains('4.46'));
    expect(requestCount, 2);
  });

  test('Data API probe confirms a selected investor flow vendor', () async {
    const repository = MockFlowRepository();
    final source = repository.dataApiSources.firstWhere(
      (source) => source.id == 'kr-investor-flow',
    );
    final client = DataApiProbeClient(
      client: MockClient((request) async {
        throw StateError('vendor selection probe should not call the network');
      }),
    );
    addTearDown(client.dispose);

    final result = await client.probe(source, '', vendorId: 'krx-data');

    expect(result.ok, isTrue);
    expect(result.provider, 'KRX');
    expect(result.endpoint, contains('KRX 데이터'));
    expect(result.message, contains('KRX 정보데이터시스템'));
  });

  test('Data API probe validates OpenDART company access', () async {
    const repository = MockFlowRepository();
    final source = repository.dataApiSources.firstWhere(
      (source) => source.id == 'opendart',
    );
    final client = DataApiProbeClient(
      client: MockClient((request) async {
        expect(request.url.host, 'opendart.fss.or.kr');
        expect(request.url.path, '/api/company.json');
        expect(request.url.queryParameters['crtfc_key'], 'dart-key');
        expect(request.url.queryParameters['corp_code'], '00126380');
        return http.Response(
          '''
{
  "status": "000",
  "message": "정상",
  "corp_name": "삼성전자(주)",
  "stock_code": "005930"
}
''',
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );
    addTearDown(client.dispose);

    final result = await client.probe(source, 'dart-key');

    expect(result.ok, isTrue);
    expect(result.endpoint, 'company.json 삼성전자');
    expect(result.message, contains('삼성전자'));
    expect(result.linkedDataLabel, contains('공시'));
  });

  test('Data API probe falls back to OpenDART local proxy', () async {
    const repository = MockFlowRepository();
    final source = repository.dataApiSources.firstWhere(
      (source) => source.id == 'opendart',
    );
    var proxyRequestCount = 0;
    final client = DataApiProbeClient(
      client: MockClient((request) async {
        if (request.url.host == 'opendart.fss.or.kr') {
          throw http.ClientException('XMLHttpRequest error.', request.url);
        }
        expect(request.url.host, '127.0.0.1');
        expect(request.url.port, 3000);
        expect(request.url.path, '/api/data-api/opendart/company');
        expect(request.url.queryParameters['crtfc_key'], 'dart-key');
        expect(request.url.queryParameters['corp_code'], '00126380');
        proxyRequestCount += 1;
        return http.Response(
          '''
{
  "status": "000",
  "message": "정상",
  "corp_name": "삼성전자(주)",
  "stock_code": "005930"
}
''',
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );
    addTearDown(client.dispose);

    final result = await client.probe(source, 'dart-key');

    expect(result.ok, isTrue);
    expect(result.message, contains('삼성전자'));
    expect(proxyRequestCount, 1);
  });

  testWidgets('Sparkline supports pinch zooming into a shorter period', (
    tester,
  ) async {
    final values = List<double>.generate(10, (index) => index.toDouble());
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 320,
            height: 90,
            child: Sparkline(values: values),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    SparklinePainter painter() {
      final customPaint = tester.widget<CustomPaint>(
        find.descendant(
          of: find.byType(Sparkline),
          matching: find.byType(CustomPaint),
        ),
      );
      return customPaint.painter! as SparklinePainter;
    }

    expect(painter().values.length, 10);

    final center = tester.getCenter(find.byType(Sparkline));
    final firstFinger = await tester.createGesture(
      pointer: 101,
      kind: PointerDeviceKind.touch,
    );
    final secondFinger = await tester.createGesture(
      pointer: 102,
      kind: PointerDeviceKind.touch,
    );
    await firstFinger.down(center - const Offset(30, 0));
    await secondFinger.down(center + const Offset(30, 0));
    await tester.pump(const Duration(milliseconds: 50));
    await firstFinger.moveTo(center - const Offset(120, 0));
    await secondFinger.moveTo(center + const Offset(120, 0));
    await tester.pump();
    await firstFinger.up();
    await secondFinger.up();
    await tester.pumpAndSettle();

    expect(painter().values.length, lessThan(10));
  });

  test('CoinGecko market items are mapped into crypto assets', () async {
    final service = CoinGeckoCryptoMarketService(
      client: MockClient((request) async {
        expect(request.url.host, 'api.coingecko.com');
        expect(request.url.path, '/api/v3/coins/markets');
        return http.Response(
          '''
[
  {
    "id": "bitcoin",
    "symbol": "btc",
    "name": "Bitcoin",
    "market_cap_rank": 1,
    "current_price": 104200.5,
    "market_cap": 2060000000000,
    "total_volume": 42000000000,
    "price_change_percentage_1h_in_currency": 0.2,
    "price_change_percentage_24h_in_currency": 1.7,
    "price_change_percentage_7d_in_currency": 4.8,
    "last_updated": "2026-06-17T20:00:00.000Z"
  }
]
''',
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );
    addTearDown(service.dispose);

    final result = await service.fetchAssets(const [
      CryptoAsset(
        id: 'bitcoin',
        symbol: 'BTC',
        name: 'Bitcoin',
        rank: 1,
        priceUsd: 100000,
        marketCapUsd: 2000000000000,
        volume24hUsd: 40000000000,
        change1hPercent: 0,
        change24hPercent: 0,
        change7dPercent: 0,
        updatedAt: null,
        provider: 'mock',
      ),
    ]);

    expect(result.snapshot.status, CryptoFetchStatus.ready);
    expect(result.assets, hasLength(1));
    expect(result.assets.single.symbol, 'BTC');
    expect(result.assets.single.priceUsd, 104200.5);
    expect(result.assets.single.change7dPercent, 4.8);
  });

  test(
    'SettingsRepository stores API keys in the local settings database',
    () async {
      final repository = SettingsRepository();
      final sources = const MockFlowRepository().dataApiSources;

      await repository.saveDataApiKeySettings(
        const DataApiKeySettings(
          keys: {'alpha-vantage': 'alpha-key', 'coingecko': 'coingecko-key'},
          vendors: {'fund-flow-vendor': 'epfr'},
        ),
        sources,
      );

      final prefs = await SharedPreferences.getInstance();
      expect(
        prefs.getString(
          LocalSettingsDatabase.dataApiKeyStorageKey('alpha-vantage'),
        ),
        'alpha-key',
      );
      expect(
        prefs.getString(
          LocalSettingsDatabase.dataApiKeyStorageKey('coingecko'),
        ),
        'coingecko-key',
      );
      expect(
        prefs.getString(
          LocalSettingsDatabase.dataApiKeyUpdatedAtStorageKey('alpha-vantage'),
        ),
        isNotEmpty,
      );
      expect(
        prefs.getString(
          LocalSettingsDatabase.dataApiVendorStorageKey('fund-flow-vendor'),
        ),
        'epfr',
      );
      expect(prefs.getInt(LocalSettingsDatabase.schemaVersionKey), 1);
      expect(prefs.getString(LocalSettingsDatabase.lastWriteAtKey), isNotEmpty);
    },
  );

  test(
    'SettingsRepository migrates legacy API keys into local database',
    () async {
      SharedPreferences.setMockInitialValues({
        'dataApi.key.alpha-vantage': 'legacy-alpha-key',
      });
      final repository = SettingsRepository();
      final sources = const MockFlowRepository().dataApiSources;

      final settings = await repository.loadDataApiKeySettings(sources);

      expect(settings.keyFor('alpha-vantage'), 'legacy-alpha-key');
      final prefs = await SharedPreferences.getInstance();
      expect(
        prefs.getString(
          LocalSettingsDatabase.dataApiKeyStorageKey('alpha-vantage'),
        ),
        'legacy-alpha-key',
      );
    },
  );

  test(
    'SettingsRepository stores Toss credentials in local database',
    () async {
      final repository = SettingsRepository();
      await repository.saveTossAccountSettings(
        const TossAccountSettings(
          enabled: true,
          accountAlias: '토스 주계좌',
          accountHint: '1234',
          apiBaseUrl: 'https://open-api.example.com',
          appKey: 'toss-app-key',
          appSecret: 'toss-secret',
          accessToken: 'token-value',
          accountNumber: '000-123',
          testPath: '/v1/accounts',
          readOnly: true,
          orderLocked: true,
        ),
      );

      final prefs = await SharedPreferences.getInstance();
      expect(
        prefs.getString(LocalSettingsDatabase.tossStorageKey('appKey')),
        'toss-app-key',
      );
      expect(
        prefs.getString(LocalSettingsDatabase.tossStorageKey('appSecret')),
        'toss-secret',
      );
      expect(
        prefs.getString(LocalSettingsDatabase.tossStorageKey('accessToken')),
        'token-value',
      );
      expect(
        prefs.getBool(LocalSettingsDatabase.tossStorageKey('enabled')),
        true,
      );
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

  testWidgets('MarketFlow saves a data API key from the field button', (
    tester,
  ) async {
    await pumpMarketFlowApp(tester);

    await tester.tap(find.text('설정'));
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('data-api-key-input-coingecko')),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('data-api-key-input-coingecko')),
      'coingecko-demo-key',
    );
    await tester.pumpAndSettle();

    expect(find.text('변경됨'), findsWidgets);
    expect(find.text('입력값 저장'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('data-api-save-coingecko')));
    await tester.pumpAndSettle();

    final prefs = await SharedPreferences.getInstance();
    expect(
      prefs.getString(LocalSettingsDatabase.dataApiKeyStorageKey('coingecko')),
      'coingecko-demo-key',
    );
    expect(find.text('저장됨'), findsWidgets);
    expect(find.text('로컬 DB에 저장된 key입니다.'), findsOneWidget);
  });

  testWidgets('MarketFlow opens on the live flow dashboard', (tester) async {
    await pumpMarketFlowApp(tester);

    expect(find.text('MarketFlow'), findsOneWidget);
    expect(
      find.byWidgetPredicate(
        (widget) =>
            widget is Text &&
            (widget.data == 'API key 필요' || widget.data == 'API 연결'),
        description: 'API key status text',
      ),
      findsWidgets,
    );
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
    expect(find.text('피드 채널'), findsOneWidget);
    expect(find.text('Google News RSS'), findsWidgets);
    expect(find.byTooltip('대표 뉴스 열기'), findsOneWidget);
    expect(find.byTooltip('채널 뉴스 열기'), findsWidgets);

    await tester.scrollUntilVisible(
      find.text('경제 피드'),
      260,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('경제 피드'), findsOneWidget);
    expect(find.text('AI CAPEX 자금이 반도체에서 전력 인프라로 확산'), findsWidgets);
    expect(find.text('MarketFlow Theme Map · 18분 전'), findsOneWidget);
    expect(find.text('news.google.com'), findsWidgets);
    expect(find.byTooltip('상세 페이지 열기'), findsWidgets);
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
    expect(find.text('보기 단위'), findsOneWidget);
    expect(find.text('일별'), findsOneWidget);
    expect(find.text('주별'), findsWidgets);
    expect(find.text('월별'), findsOneWidget);
    expect(find.text('mock 데이터'), findsWidgets);
    expect(find.text('1M'), findsOneWidget);
    expect(find.text('3M'), findsOneWidget);
    expect(find.text('ALL'), findsOneWidget);
    expect(find.text('캔들: 종합지수'), findsOneWidget);

    await tester.tap(find.byTooltip('종합 플로우 상세 보기'));
    await tester.pumpAndSettle();

    expect(find.text('종합 플로우 상세'), findsOneWidget);
    expect(find.text('현재 종합'), findsOneWidget);
    expect(find.text('최근 캔들'), findsOneWidget);

    await dismissModal(tester, find.text('종합 플로우 상세'));

    await tester.tap(find.text('일별'));
    await tester.pumpAndSettle();

    expect(find.text('일별'), findsWidgets);
    expect(find.text('mock 데이터'), findsWidgets);

    await tester.tap(find.text('월별'));
    await tester.pumpAndSettle();

    expect(find.text('월별'), findsWidgets);
    expect(find.text('mock 데이터'), findsWidgets);

    await tester.scrollUntilVisible(
      find.text('코인 마켓'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('코인 마켓'), findsOneWidget);
    expect(find.text('Bitcoin · BTC'), findsOneWidget);
    expect(find.text('Ethereum · ETH'), findsOneWidget);
    expect(find.text('공개 API'), findsOneWidget);

    await tester.tap(find.byTooltip('코인 상세 보기'));
    await tester.pumpAndSettle();

    expect(find.text('코인 마켓 상세'), findsOneWidget);
    expect(find.text('BTC 비중'), findsOneWidget);
    expect(find.text('24h 변화율'), findsOneWidget);

    await dismissModal(tester, find.text('코인 마켓 상세'));

    await tester.scrollUntilVisible(
      find.text('필요 API 맵'),
      360,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('필요 API 맵'), findsOneWidget);
    expect(find.text('Alpha Vantage'), findsOneWidget);
    expect(find.text('토스증권 Open API'), findsOneWidget);

    await tester.tap(find.byTooltip('API 상세 보기').first);
    await tester.pumpAndSettle();

    expect(find.text('연결 준비도'), findsOneWidget);
    expect(find.text('우선순위 점수'), findsOneWidget);
    expect(find.textContaining('연동 데이터:'), findsOneWidget);

    await dismissModal(tester, find.text('연결 준비도'));

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

    await tester.tap(find.byTooltip('자금 흐름 상세 보기').first);
    await tester.pumpAndSettle();

    expect(find.text('흐름 점수'), findsOneWidget);
    expect(find.text('추세 변화'), findsOneWidget);
    expect(find.text('순유입'), findsOneWidget);

    await dismissModal(tester, find.text('흐름 점수'));

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

    await tester.tap(find.byTooltip('새 흐름 상세 보기').first);
    await tester.pumpAndSettle();

    expect(find.text('현실화'), findsOneWidget);
    expect(find.textContaining('미국 브로커 상장 목록'), findsOneWidget);
    expect(find.text('수혜군'), findsOneWidget);
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
    expect(find.text('등록한 key를 기기 로컬 DB에 저장'), findsOneWidget);
    expect(find.byKey(const ValueKey('data-api-save-top')), findsOneWidget);
    expect(find.text('API key 입력 후 저장'), findsWidgets);
    expect(find.text('Alpha Vantage'), findsOneWidget);
    expect(find.text('FRED API'), findsOneWidget);
    expect(find.text('OpenDART API'), findsOneWidget);
    expect(find.text('CoinGecko API'), findsOneWidget);
    expect(find.text('DefiLlama API'), findsOneWidget);
    expect(
      find.text('발급 위치: https://www.alphavantage.co/documentation/'),
      findsOneWidget,
    );
    expect(find.text('연동 데이터: 대시보드 관심 종목 가격, 등락률, 거래량'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('data-api-test-alpha-vantage')),
      findsOneWidget,
    );
    expect(find.text('연결 테스트'), findsWidgets);
    expect(
      find.byKey(const ValueKey('data-api-test-opendart')),
      findsOneWidget,
    );
    expect(find.text('읽기 전용 데이터'), findsOneWidget);
    expect(find.text('로컬 DB 저장'), findsOneWidget);
    expect(find.text('API key'), findsWidgets);

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('data-api-test-coingecko')),
      420,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('공개 API 테스트'), findsWidgets);
    expect(
      find.text('연동 데이터: 자금 탭 코인 가격, 시총, 거래량, 1h/24h/7d 변화율'),
      findsOneWidget,
    );

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('data-api-vendor-fund-flow-vendor')),
      520,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('벤더 선택'), findsWidgets);
    expect(find.text('벤더 API key'), findsWidgets);

    await tester.tap(
      find.byKey(const ValueKey('data-api-vendor-fund-flow-vendor')),
    );
    await tester.pumpAndSettle();

    expect(find.text('EPFR Global Fund Flows'), findsOneWidget);
    expect(find.text('Nasdaq Data Link'), findsOneWidget);

    await tester.tap(find.text('EPFR Global Fund Flows'));
    await tester.pumpAndSettle();

    expect(find.textContaining('계약 후 제공되는 fund flow endpoint'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('data-api-vendor-kr-investor-flow')),
      520,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    final krTestButton = find.byKey(
      const ValueKey('data-api-test-kr-investor-flow'),
    );
    expect(tester.widget<OutlinedButton>(krTestButton).onPressed, isNull);

    await tester.tap(
      find.byKey(const ValueKey('data-api-vendor-kr-investor-flow')),
    );
    await tester.pumpAndSettle();

    expect(find.text('KRX 정보데이터시스템'), findsOneWidget);

    await tester.tap(find.text('KRX 정보데이터시스템'));
    await tester.pumpAndSettle();

    expect(tester.widget<OutlinedButton>(krTestButton).onPressed, isNotNull);

    await tester.scrollUntilVisible(
      find.text('토스증권 계정'),
      520,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();

    expect(find.text('토스증권 계정'), findsOneWidget);
    expect(find.text('앱에서 Open API 직접 호출 · 로컬 DB 저장'), findsOneWidget);
    expect(find.text('Open API 기본 URL'), findsOneWidget);
    expect(find.text('앱 키'), findsOneWidget);
    expect(find.text('앱 시크릿'), findsOneWidget);
    expect(find.text('액세스 토큰'), findsOneWidget);
    expect(find.text('연결 테스트'), findsOneWidget);
    expect(find.text('주문 기능 잠금'), findsOneWidget);
  });
}
