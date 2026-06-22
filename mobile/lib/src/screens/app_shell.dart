import 'package:flutter/material.dart';

import '../data/checklist_repository.dart';
import '../data/crypto_market_service.dart';
import '../data/economic_feed_service.dart';
import '../data/flow_repository.dart';
import '../data/market_data_api.dart';
import '../data/settings_repository.dart';
import '../data/toss_direct_api.dart';
import '../models/market_models.dart';
import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/region_switcher.dart';
import '../widgets/sparkline.dart';

class AppShell extends StatefulWidget {
  const AppShell({
    required this.repository,
    required this.themePreference,
    required this.themeSettingsLoaded,
    required this.onThemePreferenceChanged,
    this.cryptoMarketService,
    this.economicFeedService,
    super.key,
  });

  final FlowRepository repository;
  final CryptoMarketService? cryptoMarketService;
  final EconomicFeedService? economicFeedService;
  final AppThemePreference themePreference;
  final bool themeSettingsLoaded;
  final Future<void> Function(AppThemePreference preference)
  onThemePreferenceChanged;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _tabIndex = 0;
  MarketRegion _region = MarketRegion.all;
  late AppUser _currentUser;
  late List<JournalEntry> _journals;
  late ChecklistRepository _checklistRepository;
  late CryptoMarketService _cryptoMarketService;
  late EconomicFeedService _economicFeedService;
  late AlphaVantageQuoteService _quoteService;
  late TossDirectApiClient _tossDirectApiClient;
  late SettingsRepository _settingsRepository;
  late CryptoMarketSnapshot _cryptoSnapshot;
  late EconomicFeedFetchSnapshot _feedSnapshot;
  late QuoteApiSnapshot _quoteSnapshot;
  late List<CryptoAsset> _cryptoAssets;
  late List<EconomicFeedItem> _economicFeedItems;
  Map<String, LiveQuote> _quotes = const {};
  InvestmentChecklistDay _checklistDay = InvestmentChecklistDay.defaults(
    checklistDateKey(DateTime.now()),
  );
  Map<String, InvestmentChecklistDay> _checklistMonth = const {};
  DateTime _selectedChecklistDate = DateTime.now();
  DateTime _focusedChecklistMonth = checklistMonthStart(DateTime.now());
  DataApiKeySettings _dataApiKeySettings = DataApiKeySettings.empty();
  TossAccountSettings _tossSettings = TossAccountSettings.defaults();
  bool _checklistLoaded = false;
  bool _dataApiKeysLoaded = false;
  bool _settingsLoaded = false;

  @override
  void initState() {
    super.initState();
    _currentUser = widget.repository.users.first;
    _journals = widget.repository.journals.toList();
    _checklistRepository = ChecklistRepository();
    _cryptoMarketService =
        widget.cryptoMarketService ?? CoinGeckoCryptoMarketService();
    _cryptoAssets = widget.repository.cryptoAssets.toList();
    _cryptoSnapshot = _cryptoMarketService.initialSnapshot(
      _cryptoAssets.length,
    );
    _economicFeedService =
        widget.economicFeedService ?? GoogleNewsEconomicFeedService();
    _economicFeedItems = widget.repository.economicFeeds.toList();
    _feedSnapshot = EconomicFeedFetchSnapshot.idle(_economicFeedItems.length);
    _quoteService = AlphaVantageQuoteService();
    _tossDirectApiClient = TossDirectApiClient();
    _settingsRepository = SettingsRepository();
    _quoteSnapshot = _quoteService.initialSnapshot;
    _loadChecklistDate(DateTime.now());
    _loadDataApiKeySettings();
    _loadTossSettings();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _refreshLiveQuotes();
      _refreshCryptoAssets();
      _refreshEconomicFeeds();
    });
  }

  @override
  void dispose() {
    _cryptoMarketService.dispose();
    _economicFeedService.dispose();
    _quoteService.dispose();
    _tossDirectApiClient.dispose();
    super.dispose();
  }

  Future<void> _refreshEconomicFeeds() async {
    if (mounted) {
      setState(() {
        _feedSnapshot = EconomicFeedFetchSnapshot.loading(
          _economicFeedItems.length,
        );
      });
    }

    final result = await _economicFeedService.fetchFeeds();
    if (!mounted) {
      return;
    }

    setState(() {
      if (result.items.isNotEmpty) {
        _economicFeedItems = result.items;
      }
      _feedSnapshot = result.snapshot;
    });
  }

  Future<void> _refreshCryptoAssets() async {
    if (mounted) {
      setState(() {
        _cryptoSnapshot = _cryptoMarketService.loadingSnapshot(
          _cryptoAssets.length,
        );
      });
    }

    final result = await _cryptoMarketService.fetchAssets(_cryptoAssets);
    if (!mounted) {
      return;
    }

    setState(() {
      _cryptoAssets = result.assets;
      _cryptoSnapshot = result.snapshot;
    });
  }

  Future<void> _refreshLiveQuotes() async {
    final equities = widget.repository.equities.toList(growable: false)
      ..sort((a, b) => b.flowScore.compareTo(a.flowScore));
    if (mounted) {
      setState(() {
        _quoteSnapshot = _quoteService.loadingSnapshot(equities.length);
      });
    }

    final result = await _quoteService.fetchQuotes(equities);
    if (!mounted) {
      return;
    }

    setState(() {
      _quotes = result.quotes;
      _quoteSnapshot = result.snapshot;
    });
  }

  Future<void> _loadTossSettings() async {
    final settings = await _settingsRepository.loadTossAccountSettings();
    if (!mounted) {
      return;
    }
    setState(() {
      _tossSettings = settings;
      _settingsLoaded = true;
    });
  }

  Future<void> _loadChecklistDate(
    DateTime date, {
    DateTime? focusedMonth,
  }) async {
    final selectedDate = DateTime(date.year, date.month, date.day);
    final month = checklistMonthStart(focusedMonth ?? selectedDate);
    final day = await _checklistRepository.loadDay(selectedDate);
    final monthDays = await _checklistRepository.loadMonth(month);
    if (!mounted) {
      return;
    }
    setState(() {
      _selectedChecklistDate = selectedDate;
      _focusedChecklistMonth = month;
      _checklistDay = day;
      _checklistMonth = {...monthDays, day.dateKey: day};
      _checklistLoaded = true;
    });
  }

  Future<void> _saveChecklistDay(InvestmentChecklistDay day) async {
    await _checklistRepository.saveDay(day);
    final monthDays = await _checklistRepository.loadMonth(
      _focusedChecklistMonth,
    );
    if (!mounted) {
      return;
    }
    setState(() {
      _checklistDay = day;
      _checklistMonth = {...monthDays, day.dateKey: day};
      _checklistLoaded = true;
    });
  }

  Future<void> _changeChecklistMonth(DateTime month) async {
    await _loadChecklistDate(checklistMonthStart(month), focusedMonth: month);
  }

  Future<void> _toggleChecklistItem(String itemId, bool checked) async {
    await _saveChecklistDay(_checklistDay.toggleItem(itemId, checked));
  }

  Future<void> _addChecklistItem(String label) async {
    final itemId = 'custom-${DateTime.now().microsecondsSinceEpoch}';
    await _saveChecklistDay(_checklistDay.addCustomItem(itemId, label));
  }

  Future<void> _removeChecklistItem(String itemId) async {
    await _saveChecklistDay(_checklistDay.removeItem(itemId));
  }

  Future<void> _saveChecklistNote(String note) async {
    await _saveChecklistDay(_checklistDay.copyWith(note: note.trim()));
  }

  Future<void> _resetChecklistDay() async {
    await _checklistRepository.resetDay(_checklistDay.dateKey);
    await _loadChecklistDate(
      _selectedChecklistDate,
      focusedMonth: _focusedChecklistMonth,
    );
  }

  Future<void> _loadDataApiKeySettings() async {
    final settings = await _settingsRepository.loadDataApiKeySettings(
      widget.repository.dataApiSources,
    );
    _quoteService.updateApiKey(settings.keyFor('alpha-vantage'));
    _cryptoMarketService.updateApiKey(settings.keyFor('coingecko'));
    if (!mounted) {
      return;
    }
    setState(() {
      _dataApiKeySettings = settings;
      _dataApiKeysLoaded = true;
      _quoteSnapshot = _quoteService.initialSnapshot;
      _cryptoSnapshot = _cryptoMarketService.initialSnapshot(
        _cryptoAssets.length,
      );
    });
    if (settings.hasKeyFor('alpha-vantage')) {
      await _refreshLiveQuotes();
    }
    await _refreshCryptoAssets();
  }

  Future<void> _saveDataApiKeySettings(DataApiKeySettings settings) async {
    final previousAlphaKey = _dataApiKeySettings.keyFor('alpha-vantage');
    final previousCoinGeckoKey = _dataApiKeySettings.keyFor('coingecko');
    await _settingsRepository.saveDataApiKeySettings(
      settings,
      widget.repository.dataApiSources,
    );
    final nextAlphaKey = settings.keyFor('alpha-vantage');
    final nextCoinGeckoKey = settings.keyFor('coingecko');
    _quoteService.updateApiKey(nextAlphaKey);
    _cryptoMarketService.updateApiKey(nextCoinGeckoKey);
    if (!mounted) {
      return;
    }
    setState(() {
      _dataApiKeySettings = settings;
      if (previousAlphaKey != nextAlphaKey) {
        if (nextAlphaKey.isEmpty) {
          _quotes = const {};
        }
        _quoteSnapshot = _quoteService.initialSnapshot;
      }
      if (previousCoinGeckoKey != nextCoinGeckoKey) {
        _cryptoSnapshot = _cryptoMarketService.initialSnapshot(
          _cryptoAssets.length,
        );
      }
    });
    if (previousAlphaKey != nextAlphaKey && nextAlphaKey.isNotEmpty) {
      await _refreshLiveQuotes();
    }
    if (previousCoinGeckoKey != nextCoinGeckoKey) {
      await _refreshCryptoAssets();
    }
  }

  Future<void> _saveTossSettings(TossAccountSettings settings) async {
    await _settingsRepository.saveTossAccountSettings(settings);
    if (!mounted) {
      return;
    }
    setState(() => _tossSettings = settings);
  }

  Future<TossDirectApiProbeResult> _testTossConnection(
    TossAccountSettings settings,
  ) async {
    await _saveTossSettings(settings);
    return _tossDirectApiClient.probe(settings);
  }

  bool _matchesRegion(MarketRegion region) {
    return _region == MarketRegion.all || _region == region;
  }

  List<MarketPulse> get _pulses {
    return widget.repository.marketPulses
        .where((pulse) => _matchesRegion(pulse.region))
        .toList(growable: false);
  }

  List<EconomicFeedItem> get _economicFeeds {
    return _economicFeedItems
        .where((feed) => _matchesRegion(feed.region))
        .toList(growable: false)
      ..sort(_compareEconomicFeeds);
  }

  List<ThemePulse> get _themes {
    return widget.repository.themes
        .where((theme) => _matchesRegion(theme.region))
        .toList(growable: false)
      ..sort((a, b) => b.score.compareTo(a.score));
  }

  List<EquityFlow> get _equities {
    return widget.repository.equities
        .where((equity) => _matchesRegion(equity.region))
        .toList(growable: false)
      ..sort((a, b) => b.flowScore.compareTo(a.flowScore));
  }

  List<JournalEntry> get _userJournals {
    return _journals
        .where((entry) => entry.userId == _currentUser.id)
        .toList(growable: false);
  }

  Future<void> _openJournalComposer() async {
    final entry = await showModalBottomSheet<JournalEntry>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) {
        return JournalComposer(
          user: _currentUser,
          equities: widget.repository.equities,
        );
      },
    );

    if (entry != null) {
      setState(() => _journals.insert(0, entry));
    }
  }

  @override
  Widget build(BuildContext context) {
    final screens = [
      DashboardScreen(
        user: _currentUser,
        pulses: _pulses,
        themes: _themes,
        equities: _equities,
        quotes: _quotes,
        quoteSnapshot: _quoteSnapshot,
        onRefreshQuotes: _refreshLiveQuotes,
      ),
      EconomicFeedScreen(
        feeds: _economicFeeds,
        feedSnapshot: _feedSnapshot,
        onRefreshFeeds: _refreshEconomicFeeds,
        pulses: _pulses,
        flows: widget.repository.capitalFlows,
        themes: _themes,
        apiSources: widget.repository.dataApiSources,
        quoteSnapshot: _quoteSnapshot,
      ),
      CapitalFlowScreen(
        apiSources: widget.repository.dataApiSources,
        candles: widget.repository.globalFlowCandles,
        flows: widget.repository.capitalFlows,
        cryptoAssets: _cryptoAssets,
        cryptoSnapshot: _cryptoSnapshot,
        onRefreshCryptoAssets: _refreshCryptoAssets,
        emergingFlows: widget.repository.emergingCapitalFlows,
      ),
      ThemeBoardScreen(themes: _themes),
      WatchlistScreen(equities: _equities, quotes: _quotes),
      InvestmentChecklistScreen(
        day: _checklistDay,
        selectedDate: _selectedChecklistDate,
        focusedMonth: _focusedChecklistMonth,
        monthDays: _checklistMonth,
        loaded: _checklistLoaded,
        pulses: _pulses,
        capitalFlows: widget.repository.capitalFlows,
        themes: _themes,
        equities: _equities,
        quotes: _quotes,
        quoteSnapshot: _quoteSnapshot,
        onRefreshQuotes: _refreshLiveQuotes,
        onDateSelected: (date) =>
            _loadChecklistDate(date, focusedMonth: _focusedChecklistMonth),
        onMonthChanged: _changeChecklistMonth,
        onTodaySelected: () => _loadChecklistDate(DateTime.now()),
        onItemChanged: _toggleChecklistItem,
        onAddItem: _addChecklistItem,
        onRemoveItem: _removeChecklistItem,
        onSaveNote: _saveChecklistNote,
        onResetDay: _resetChecklistDay,
      ),
      JournalScreen(entries: _userJournals, onAddEntry: _openJournalComposer),
      SettingsScreen(
        apiSources: widget.repository.dataApiSources,
        dataApiKeySettings: _dataApiKeySettings,
        dataApiKeysLoaded: _dataApiKeysLoaded,
        onSaveDataApiKeySettings: _saveDataApiKeySettings,
        tossSettings: _tossSettings,
        settingsLoaded: _settingsLoaded,
        onSaveTossSettings: _saveTossSettings,
        onTestTossConnection: _testTossConnection,
        themePreference: widget.themePreference,
        themeSettingsLoaded: widget.themeSettingsLoaded,
        onThemePreferenceChanged: widget.onThemePreferenceChanged,
      ),
    ];

    return Scaffold(
      body: Column(
        children: [
          SafeArea(
            bottom: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _ShellHeader(
                    currentUser: _currentUser,
                    users: widget.repository.users,
                    onUserChanged: (user) {
                      setState(() => _currentUser = user);
                    },
                  ),
                  const SizedBox(height: 14),
                  RegionSwitcher(
                    value: _region,
                    onChanged: (region) => setState(() => _region = region),
                  ),
                ],
              ),
            ),
          ),
          Expanded(
            child: IndexedStack(index: _tabIndex, children: screens),
          ),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tabIndex,
        onDestinationSelected: (index) => setState(() => _tabIndex = index),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.dashboard_outlined),
            selectedIcon: Icon(Icons.dashboard),
            label: '흐름',
          ),
          NavigationDestination(
            icon: Icon(Icons.dynamic_feed_outlined),
            selectedIcon: Icon(Icons.dynamic_feed),
            label: '피드',
          ),
          NavigationDestination(
            icon: Icon(Icons.public_outlined),
            selectedIcon: Icon(Icons.public),
            label: '자금',
          ),
          NavigationDestination(
            icon: Icon(Icons.bubble_chart_outlined),
            selectedIcon: Icon(Icons.bubble_chart),
            label: '테마',
          ),
          NavigationDestination(
            icon: Icon(Icons.format_list_bulleted),
            selectedIcon: Icon(Icons.playlist_add_check),
            label: '관심',
          ),
          NavigationDestination(
            icon: Icon(Icons.fact_check_outlined),
            selectedIcon: Icon(Icons.fact_check),
            label: '체크',
          ),
          NavigationDestination(
            icon: Icon(Icons.edit_note_outlined),
            selectedIcon: Icon(Icons.edit_note),
            label: '기록',
          ),
          NavigationDestination(
            icon: Icon(Icons.settings_outlined),
            selectedIcon: Icon(Icons.settings),
            label: '설정',
          ),
        ],
      ),
    );
  }
}

class _ShellHeader extends StatelessWidget {
  const _ShellHeader({
    required this.currentUser,
    required this.users,
    required this.onUserChanged,
  });

  final AppUser currentUser;
  final List<AppUser> users;
  final ValueChanged<AppUser> onUserChanged;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'MarketFlow',
                style: Theme.of(context).textTheme.displaySmall,
              ),
              const SizedBox(height: 4),
              Text(
                '${currentUser.role} · ${currentUser.riskProfile}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
          ),
        ),
        PopupMenuButton<AppUser>(
          tooltip: '프로필',
          onSelected: onUserChanged,
          itemBuilder: (context) {
            return [
              for (final user in users)
                PopupMenuItem<AppUser>(
                  value: user,
                  child: Row(
                    children: [
                      CircleAvatar(
                        radius: 14,
                        backgroundColor: user.id == currentUser.id
                            ? AppColors.green
                            : AppColors.line,
                        child: Text(
                          user.name.characters.first,
                          style: TextStyle(
                            color: user.id == currentUser.id
                                ? Colors.white
                                : AppColors.ink,
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(user.name),
                            Text(
                              '${user.watchlistCount} symbols',
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
            ];
          },
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: AppColors.surface,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: AppColors.line),
            ),
            child: Padding(
              padding: const EdgeInsets.all(8),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  CircleAvatar(
                    radius: 16,
                    backgroundColor: AppColors.blue,
                    child: Text(
                      currentUser.name.characters.first,
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  const Icon(Icons.expand_more, size: 18),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class DashboardScreen extends StatelessWidget {
  const DashboardScreen({
    required this.user,
    required this.pulses,
    required this.themes,
    required this.equities,
    required this.quotes,
    required this.quoteSnapshot,
    required this.onRefreshQuotes,
    super.key,
  });

  final AppUser user;
  final List<MarketPulse> pulses;
  final List<ThemePulse> themes;
  final List<EquityFlow> equities;
  final Map<String, LiveQuote> quotes;
  final QuoteApiSnapshot quoteSnapshot;
  final VoidCallback onRefreshQuotes;

  @override
  Widget build(BuildContext context) {
    final averageScore = equities.isEmpty
        ? 0
        : (equities.map((equity) => equity.flowScore).reduce((a, b) => a + b) /
                  equities.length)
              .round();

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
      children: [
        Row(
          children: [
            MetricPill(
              icon: Icons.auto_graph,
              label: '평균 흐름',
              value: '$averageScore',
              color: scoreColor(averageScore),
            ),
            const SizedBox(width: 10),
            MetricPill(
              icon: Icons.hub_outlined,
              label: '강세 테마',
              value: '${themes.where((theme) => theme.score >= 75).length}',
              color: AppColors.blue,
            ),
            const SizedBox(width: 10),
            MetricPill(
              icon: Icons.notifications_active_outlined,
              label: '신호 종목',
              value:
                  '${equities.where((equity) => equity.flowScore >= 82).length}',
              color: AppColors.amber,
            ),
          ],
        ),
        const SizedBox(height: 18),
        ApiStatusCard(
          snapshot: quoteSnapshot,
          liveQuoteCount: quotes.length,
          onRefresh: onRefreshQuotes,
        ),
        const SizedBox(height: 18),
        const SectionHeader(title: '시장 펄스'),
        const SizedBox(height: 10),
        if (pulses.isEmpty)
          const EmptyState(message: '선택한 시장의 흐름 데이터가 없습니다.')
        else
          ...pulses.map(
            (pulse) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: MarketPulseCard(pulse: pulse),
            ),
          ),
        const SizedBox(height: 8),
        SectionHeader(
          title: '테마 확산',
          trailing: FlowChip(
            label: '${themes.length} themes',
            color: AppColors.blue,
          ),
        ),
        const SizedBox(height: 10),
        SizedBox(
          height: 238,
          child: themes.isEmpty
              ? const EmptyState(message: '선택한 시장의 테마 데이터가 없습니다.')
              : ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: themes.length,
                  separatorBuilder: (_, _) => const SizedBox(width: 10),
                  itemBuilder: (context, index) {
                    return SizedBox(
                      width: 270,
                      child: ThemePulseCard(
                        theme: themes[index],
                        compact: true,
                      ),
                    );
                  },
                ),
        ),
        const SizedBox(height: 22),
        const SectionHeader(title: '오늘의 신호'),
        const SizedBox(height: 10),
        if (equities.isEmpty)
          const EmptyState(message: '선택한 시장의 관심 종목이 없습니다.')
        else
          ...equities
              .take(4)
              .map(
                (equity) => Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: EquityFlowTile(
                    equity: equity,
                    quote: quotes[equity.symbol],
                  ),
                ),
              ),
      ],
    );
  }
}

class EconomicFeedScreen extends StatefulWidget {
  const EconomicFeedScreen({
    required this.feeds,
    required this.feedSnapshot,
    required this.onRefreshFeeds,
    required this.pulses,
    required this.flows,
    required this.themes,
    required this.apiSources,
    required this.quoteSnapshot,
    super.key,
  });

  final List<EconomicFeedItem> feeds;
  final EconomicFeedFetchSnapshot feedSnapshot;
  final Future<void> Function() onRefreshFeeds;
  final List<MarketPulse> pulses;
  final List<CapitalFlow> flows;
  final List<ThemePulse> themes;
  final List<DataApiSource> apiSources;
  final QuoteApiSnapshot quoteSnapshot;

  @override
  State<EconomicFeedScreen> createState() => _EconomicFeedScreenState();
}

class _EconomicFeedScreenState extends State<EconomicFeedScreen> {
  String _filter = 'all';

  @override
  Widget build(BuildContext context) {
    final rankedFeeds = widget.feeds.toList(growable: false)
      ..sort(_compareEconomicFeeds);
    final filteredFeeds = _filter == 'all'
        ? rankedFeeds
        : rankedFeeds
              .where((feed) => feed.type.name == _filter)
              .toList(growable: false);
    final topImpact = rankedFeeds.isEmpty ? null : rankedFeeds.first;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
      children: [
        Row(
          children: [
            MetricPill(
              icon: Icons.auto_graph,
              label: '경제 영향',
              value: topImpact == null ? '-' : '${topImpact.impactScore}',
              color: topImpact == null
                  ? AppColors.muted
                  : scoreColor(topImpact.impactScore),
            ),
            const SizedBox(width: 10),
            MetricPill(
              icon: Icons.dynamic_feed_outlined,
              label: '피드',
              value: '${rankedFeeds.length}',
              color: AppColors.blue,
            ),
            const SizedBox(width: 10),
            MetricPill(
              icon: Icons.api_outlined,
              label: '실제 데이터',
              value: widget.feedSnapshot.statusLabel,
              color: _feedStatusColor(widget.feedSnapshot.status),
            ),
          ],
        ),
        const SizedBox(height: 18),
        EconomicFeedSummaryCard(
          feeds: rankedFeeds,
          pulses: widget.pulses,
          flows: widget.flows,
          themes: widget.themes,
          feedSnapshot: widget.feedSnapshot,
          quoteSnapshot: widget.quoteSnapshot,
        ),
        const SizedBox(height: 12),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'all', label: Text('전체')),
              ButtonSegment(value: 'macro', label: Text('매크로')),
              ButtonSegment(value: 'liquidity', label: Text('유동성')),
              ButtonSegment(value: 'policy', label: Text('정책')),
              ButtonSegment(value: 'flow', label: Text('자금')),
              ButtonSegment(value: 'earnings', label: Text('실적')),
              ButtonSegment(value: 'risk', label: Text('리스크')),
            ],
            selected: {_filter},
            onSelectionChanged: (values) => setState(() {
              _filter = values.first;
            }),
          ),
        ),
        const SizedBox(height: 12),
        SectionHeader(
          title: '경제 피드',
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              FlowChip(
                label: '${filteredFeeds.length} items',
                color: AppColors.charcoal,
              ),
              const SizedBox(width: 6),
              IconButton(
                tooltip: '실제 피드 새로고침',
                onPressed: widget.onRefreshFeeds,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        if (filteredFeeds.isEmpty)
          const EmptyState(message: '선택한 필터의 경제 피드가 없습니다.')
        else
          ...filteredFeeds.map(
            (feed) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: EconomicFeedCard(feed: feed),
            ),
          ),
      ],
    );
  }
}

class EconomicFeedSummaryCard extends StatelessWidget {
  const EconomicFeedSummaryCard({
    required this.feeds,
    required this.pulses,
    required this.flows,
    required this.themes,
    required this.feedSnapshot,
    required this.quoteSnapshot,
    super.key,
  });

  final List<EconomicFeedItem> feeds;
  final List<MarketPulse> pulses;
  final List<CapitalFlow> flows;
  final List<ThemePulse> themes;
  final EconomicFeedFetchSnapshot feedSnapshot;
  final QuoteApiSnapshot quoteSnapshot;

  @override
  Widget build(BuildContext context) {
    final topFeed = feeds.isEmpty ? null : feeds.first;
    final topPulse = pulses.toList(growable: false)
      ..sort((a, b) => b.score.compareTo(a.score));
    final topFlow = flows.toList(growable: false)
      ..sort((a, b) => b.flowScore.compareTo(a.flowScore));
    final topTheme = themes.toList(growable: false)
      ..sort((a, b) => b.score.compareTo(a.score));
    final pulse = topPulse.isEmpty ? null : topPulse.first;
    final flow = topFlow.isEmpty ? null : topFlow.first;
    final theme = topTheme.isEmpty ? null : topTheme.first;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DecoratedBox(
                decoration: BoxDecoration(
                  color: AppColors.blue.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Icon(
                    Icons.language_outlined,
                    color: AppColors.blue,
                    size: 22,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '경제가 돌아가는 방향',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 5),
                    Text(
                      topFeed?.title ?? '경제 피드 데이터 없음',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              FlowChip(
                label: feedSnapshot.statusLabel,
                color: _feedStatusColor(feedSnapshot.status),
              ),
            ],
          ),
          const SizedBox(height: 14),
          if (topFeed != null)
            Text(topFeed.summary, style: Theme.of(context).textTheme.bodyLarge),
          const SizedBox(height: 14),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (pulse != null)
                FlowChip(
                  label: '${pulse.title} ${pulse.score}',
                  color: scoreColor(pulse.score),
                ),
              if (flow != null)
                FlowChip(
                  label: '${flow.name} ${flow.flowScore}',
                  color: _assetClassColor(flow.assetClass),
                ),
              if (theme != null)
                FlowChip(
                  label: '${theme.name} ${theme.score}',
                  color: scoreColor(theme.score),
                ),
              FlowChip(
                label: feedSnapshot.provider,
                color: _feedStatusColor(feedSnapshot.status),
              ),
              FlowChip(label: feedSnapshot.message, color: AppColors.charcoal),
              FlowChip(
                label: quoteSnapshot.statusLabel,
                color: quoteSnapshot.apiKeyConfigured
                    ? AppColors.green
                    : AppColors.amber,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class EconomicFeedCard extends StatelessWidget {
  const EconomicFeedCard({required this.feed, super.key});

  final EconomicFeedItem feed;

  @override
  Widget build(BuildContext context) {
    final color = _feedTypeColor(feed.type);
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DecoratedBox(
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Icon(_feedTypeIcon(feed.type), color: color, size: 22),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      feed.title,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 5),
                    Text(
                      '${feed.source} · ${feed.timestampLabel}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              FlowChip(label: '${feed.impactScore}', color: color),
            ],
          ),
          const SizedBox(height: 12),
          Text(feed.summary, style: Theme.of(context).textTheme.bodyLarge),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FlowChip(label: feed.type.label, color: color),
              FlowChip(label: feed.region.label, color: AppColors.charcoal),
              if (feed.url.isNotEmpty)
                FlowChip(label: _feedUrlHost(feed.url), color: AppColors.muted),
              for (final tag in feed.tags)
                FlowChip(label: tag, color: AppColors.blue),
            ],
          ),
        ],
      ),
    );
  }
}

class CapitalFlowScreen extends StatefulWidget {
  const CapitalFlowScreen({
    required this.apiSources,
    required this.candles,
    required this.flows,
    required this.cryptoAssets,
    required this.cryptoSnapshot,
    required this.onRefreshCryptoAssets,
    required this.emergingFlows,
    super.key,
  });

  final List<DataApiSource> apiSources;
  final List<FlowCandle> candles;
  final List<CapitalFlow> flows;
  final List<CryptoAsset> cryptoAssets;
  final CryptoMarketSnapshot cryptoSnapshot;
  final Future<void> Function() onRefreshCryptoAssets;
  final List<EmergingCapitalFlow> emergingFlows;

  @override
  State<CapitalFlowScreen> createState() => _CapitalFlowScreenState();
}

class _CapitalFlowScreenState extends State<CapitalFlowScreen> {
  int _rangeWeeks = 12;
  RangeValues _detailWindow = const RangeValues(0, 1);

  @override
  Widget build(BuildContext context) {
    final rankedApiSources = widget.apiSources.toList(growable: false)
      ..sort((a, b) => a.priority.compareTo(b.priority));
    final rankedFlows = widget.flows.toList(growable: false)
      ..sort((a, b) => b.flowScore.compareTo(a.flowScore));
    final rankedEmerging = widget.emergingFlows.toList(growable: false)
      ..sort((a, b) => b.probability.compareTo(a.probability));
    final rangeCandles = _rangeCandles(widget.candles, _rangeWeeks);
    final visibleCandles = _visibleCandles(rangeCandles, _detailWindow);
    final topFlow = rankedFlows.isEmpty ? null : rankedFlows.first;
    final riskOnCount = rankedFlows.where((flow) {
      return flow.assetClass == CapitalFlowAssetClass.crypto ||
          flow.assetClass == CapitalFlowAssetClass.sector ||
          flow.assetClass == CapitalFlowAssetClass.equityIndex;
    }).length;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
      children: [
        Row(
          children: [
            MetricPill(
              icon: Icons.trending_up,
              label: '최강 흐름',
              value: topFlow == null ? '-' : '${topFlow.flowScore}',
              color: topFlow == null
                  ? AppColors.muted
                  : scoreColor(topFlow.flowScore),
            ),
            const SizedBox(width: 10),
            MetricPill(
              icon: Icons.bolt_outlined,
              label: '위험자산',
              value: '$riskOnCount',
              color: AppColors.amber,
            ),
            const SizedBox(width: 10),
            MetricPill(
              icon: Icons.auto_awesome_outlined,
              label: '새 후보',
              value: '${rankedEmerging.length}',
              color: AppColors.blue,
            ),
          ],
        ),
        const SizedBox(height: 18),
        FlowCompositeChartCard(
          candles: visibleCandles,
          rangeWeeks: _rangeWeeks,
          detailWindow: _detailWindow,
          totalCount: rangeCandles.length,
          availableCount: widget.candles.length,
          onRangeChanged: (weeks) {
            setState(() {
              _rangeWeeks = weeks;
              _detailWindow = const RangeValues(0, 1);
            });
          },
          onWindowChanged: (window) {
            setState(() => _detailWindow = window);
          },
        ),
        const SizedBox(height: 18),
        CryptoMarketCard(
          assets: widget.cryptoAssets,
          snapshot: widget.cryptoSnapshot,
          onRefresh: widget.onRefreshCryptoAssets,
        ),
        const SizedBox(height: 18),
        SectionHeader(
          title: '필요 API 맵',
          trailing: FlowChip(
            label:
                '${rankedApiSources.where((api) => api.status == ApiIntegrationStatus.live || api.status == ApiIntegrationStatus.configurable).length}/${rankedApiSources.length} ready',
            color: AppColors.blue,
          ),
        ),
        const SizedBox(height: 10),
        if (rankedApiSources.isEmpty)
          const EmptyState(message: '표시할 API 소스가 없습니다.')
        else
          ...rankedApiSources.map(
            (api) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: DataApiSourceCard(api: api),
            ),
          ),
        const SizedBox(height: 8),
        SectionHeader(
          title: '세계 자금 흐름',
          trailing: FlowChip(
            label: '${rankedFlows.length} flows',
            color: AppColors.charcoal,
          ),
        ),
        const SizedBox(height: 10),
        if (rankedFlows.isEmpty)
          const EmptyState(message: '세계 자금 흐름 데이터가 없습니다.')
        else
          ...rankedFlows.map(
            (flow) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: CapitalFlowCard(flow: flow),
            ),
          ),
        const SizedBox(height: 8),
        SectionHeader(
          title: '새 흐름 후보',
          trailing: FlowChip(
            label:
                '${rankedEmerging.where((flow) => flow.probability >= 70).length} high',
            color: AppColors.green,
          ),
        ),
        const SizedBox(height: 10),
        if (rankedEmerging.isEmpty)
          const EmptyState(message: '새롭게 만들어질 자금 흐름 후보가 없습니다.')
        else
          ...rankedEmerging.map(
            (flow) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: EmergingFlowCard(flow: flow),
            ),
          ),
      ],
    );
  }

  List<FlowCandle> _rangeCandles(List<FlowCandle> candles, int weeks) {
    if (candles.length <= weeks) {
      return candles;
    }
    return candles.skip(candles.length - weeks).toList(growable: false);
  }

  List<FlowCandle> _visibleCandles(
    List<FlowCandle> candles,
    RangeValues window,
  ) {
    if (candles.length <= 4) {
      return candles;
    }
    final maxStart = candles.length - 2;
    final start = (window.start * maxStart).floor().clamp(0, maxStart).toInt();
    final end = (window.end * (candles.length - 1))
        .ceil()
        .clamp(start + 1, candles.length - 1)
        .toInt();
    return candles.sublist(start, end + 1);
  }
}

class CryptoMarketCard extends StatelessWidget {
  const CryptoMarketCard({
    required this.assets,
    required this.snapshot,
    required this.onRefresh,
    super.key,
  });

  final List<CryptoAsset> assets;
  final CryptoMarketSnapshot snapshot;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final rankedAssets = assets.toList(growable: false)
      ..sort((a, b) => a.rank.compareTo(b.rank));
    final totalMarketCap = rankedAssets.fold<double>(
      0,
      (sum, asset) => sum + asset.marketCapUsd,
    );
    final totalVolume = rankedAssets.fold<double>(
      0,
      (sum, asset) => sum + asset.volume24hUsd,
    );
    final bitcoin = rankedAssets
        .where((asset) => asset.id == 'bitcoin')
        .firstOrNull;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DecoratedBox(
                decoration: BoxDecoration(
                  color: AppColors.amber.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Icon(
                    Icons.currency_bitcoin,
                    color: AppColors.amber,
                    size: 22,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '코인 마켓',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 5),
                    Text(
                      '${snapshot.provider} · ${snapshot.endpoint}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: '코인 데이터 새로고침',
                onPressed: onRefresh,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FlowChip(
                label: snapshot.statusLabel,
                color: _cryptoStatusColor(snapshot.status),
              ),
              FlowChip(
                label: snapshot.apiKeyConfigured ? 'API key 적용' : '공개 API',
                color: snapshot.apiKeyConfigured
                    ? AppColors.green
                    : AppColors.amber,
              ),
              FlowChip(
                label: _formatCryptoUpdated(snapshot.updatedAt),
                color: AppColors.charcoal,
              ),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: _CryptoStat(
                  label: 'BTC',
                  value: bitcoin == null
                      ? '-'
                      : _formatUsdPrice(bitcoin.priceUsd),
                  color: bitcoin == null
                      ? AppColors.muted
                      : _cryptoChangeColor(bitcoin.change24hPercent),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _CryptoStat(
                  label: '추적 시총',
                  value: _formatUsdCompact(totalMarketCap),
                  color: AppColors.blue,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _CryptoStat(
                  label: '24h 거래',
                  value: _formatUsdCompact(totalVolume),
                  color: AppColors.green,
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          if (rankedAssets.isEmpty)
            const EmptyState(message: '표시할 코인 데이터가 없습니다.')
          else
            ...rankedAssets.map(
              (asset) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: CryptoAssetRow(
                  asset: asset,
                  totalMarketCap: totalMarketCap,
                ),
              ),
            ),
          Text(snapshot.message, style: Theme.of(context).textTheme.bodyMedium),
        ],
      ),
    );
  }
}

class _CryptoStat extends StatelessWidget {
  const _CryptoStat({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.canvas,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.line),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: 4),
            Text(
              value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(
                context,
              ).textTheme.titleMedium?.copyWith(color: color),
            ),
          ],
        ),
      ),
    );
  }
}

class CryptoAssetRow extends StatelessWidget {
  const CryptoAssetRow({
    required this.asset,
    required this.totalMarketCap,
    super.key,
  });

  final CryptoAsset asset;
  final double totalMarketCap;

  @override
  Widget build(BuildContext context) {
    final changeColor = _cryptoChangeColor(asset.change24hPercent);
    final dominance = totalMarketCap <= 0
        ? 0
        : asset.marketCapUsd / totalMarketCap * 100;

    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.canvas,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.line),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _SymbolAvatar(symbol: asset.symbol, color: changeColor),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${asset.name} · ${asset.symbol}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '#${asset.rank} · ${asset.provider}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                    ],
                  ),
                ),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(
                      _formatUsdPrice(asset.priceUsd),
                      style: Theme.of(context).textTheme.labelLarge,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      _formatPercentSigned(asset.change24hPercent),
                      style: TextStyle(
                        color: changeColor,
                        fontSize: 12,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FlowChip(
                  label: '1h ${_formatPercentSigned(asset.change1hPercent)}',
                  color: _cryptoChangeColor(asset.change1hPercent),
                ),
                FlowChip(
                  label: '24h ${_formatPercentSigned(asset.change24hPercent)}',
                  color: _cryptoChangeColor(asset.change24hPercent),
                ),
                FlowChip(
                  label: '7d ${_formatPercentSigned(asset.change7dPercent)}',
                  color: _cryptoChangeColor(asset.change7dPercent),
                ),
                FlowChip(
                  label: '시총 ${_formatUsdCompact(asset.marketCapUsd)}',
                  color: AppColors.blue,
                ),
                FlowChip(
                  label: '거래 ${_formatUsdCompact(asset.volume24hUsd)}',
                  color: AppColors.green,
                ),
                FlowChip(
                  label: '비중 ${dominance.toStringAsFixed(1)}%',
                  color: AppColors.charcoal,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class FlowCompositeChartCard extends StatefulWidget {
  const FlowCompositeChartCard({
    required this.candles,
    required this.rangeWeeks,
    required this.detailWindow,
    required this.totalCount,
    required this.availableCount,
    required this.onRangeChanged,
    required this.onWindowChanged,
    super.key,
  });

  final List<FlowCandle> candles;
  final int rangeWeeks;
  final RangeValues detailWindow;
  final int totalCount;
  final int availableCount;
  final ValueChanged<int> onRangeChanged;
  final ValueChanged<RangeValues> onWindowChanged;

  @override
  State<FlowCompositeChartCard> createState() => _FlowCompositeChartCardState();
}

class _FlowCompositeChartCardState extends State<FlowCompositeChartCard> {
  RangeValues _gestureStartWindow = const RangeValues(0, 1);
  final Map<int, Offset> _chartPointers = {};
  double? _pinchStartDistance;
  double _pinchStartFocalFraction = 0.5;

  double get _minChartWindowSpan {
    if (widget.totalCount <= 4) {
      return 1;
    }
    return (4 / widget.totalCount).clamp(0.0, 1.0).toDouble();
  }

  static RangeValues _scaledChartWindow({
    required RangeValues startWindow,
    required double scale,
    required double focalFraction,
    required double panFraction,
    required double minSpan,
  }) {
    final startSpan = startWindow.end - startWindow.start;
    final nextSpan = (startSpan / scale.clamp(0.35, 4.0))
        .clamp(minSpan, 1.0)
        .toDouble();
    final anchor = startWindow.start + startSpan * focalFraction;
    final nextStart = anchor - nextSpan * focalFraction + panFraction;
    return _normalizeChartWindow(
      RangeValues(nextStart, nextStart + nextSpan),
      minSpan,
    );
  }

  static RangeValues _normalizeChartWindow(RangeValues window, double minSpan) {
    final span = (window.end - window.start).clamp(minSpan, 1.0).toDouble();
    if (span >= 1) {
      return const RangeValues(0, 1);
    }
    final start = window.start.clamp(0.0, 1.0 - span).toDouble();
    return RangeValues(start, start + span);
  }

  static RangeValues _interpolateChartWindow(
    RangeValues current,
    RangeValues target,
    double factor,
  ) {
    final clampedFactor = factor.clamp(0.0, 1.0).toDouble();
    return RangeValues(
      current.start + (target.start - current.start) * clampedFactor,
      current.end + (target.end - current.end) * clampedFactor,
    );
  }

  void _handleChartPointerDown(PointerDownEvent event, double width) {
    _chartPointers[event.pointer] = event.localPosition;
    if (_chartPointers.length == 2) {
      _beginChartPinch(width);
    }
  }

  void _handleChartPointerMove(PointerMoveEvent event, double width) {
    if (!_chartPointers.containsKey(event.pointer)) {
      return;
    }
    _chartPointers[event.pointer] = event.localPosition;
    if (_chartPointers.length == 2 && _pinchStartDistance != null) {
      _updateChartPinch(width);
    }
  }

  void _handleChartPointerEnd(PointerEvent event) {
    _chartPointers.remove(event.pointer);
    if (_chartPointers.length < 2) {
      _pinchStartDistance = null;
    }
  }

  void _beginChartPinch(double width) {
    final points = _chartPointers.values.take(2).toList(growable: false);
    final distance = (points[1] - points[0]).distance;
    if (distance <= 0) {
      return;
    }
    _gestureStartWindow = widget.detailWindow;
    _pinchStartDistance = distance;
    _pinchStartFocalFraction = ((points[0].dx + points[1].dx) / 2 / width)
        .clamp(0.0, 1.0)
        .toDouble();
  }

  void _updateChartPinch(double width) {
    final startDistance = _pinchStartDistance;
    if (startDistance == null || startDistance <= 0) {
      return;
    }
    final points = _chartPointers.values.take(2).toList(growable: false);
    final distance = (points[1] - points[0]).distance;
    final currentFocalFraction = ((points[0].dx + points[1].dx) / 2 / width)
        .clamp(0.0, 1.0)
        .toDouble();
    final startSpan = _gestureStartWindow.end - _gestureStartWindow.start;
    final panFraction =
        -(currentFocalFraction - _pinchStartFocalFraction) * startSpan;
    final targetWindow = _scaledChartWindow(
      startWindow: _gestureStartWindow,
      scale: distance / startDistance,
      focalFraction: _pinchStartFocalFraction,
      panFraction: panFraction,
      minSpan: _minChartWindowSpan,
    );
    widget.onWindowChanged(
      _interpolateChartWindow(widget.detailWindow, targetWindow, 0.45),
    );
  }

  @override
  Widget build(BuildContext context) {
    final latest = widget.candles.isEmpty ? null : widget.candles.last;
    final first = widget.candles.isEmpty ? null : widget.candles.first;
    final change = latest == null || first == null
        ? 0
        : latest.close - first.open;
    final allRange = widget.availableCount <= 0 ? 18 : widget.availableCount;
    final rangeSegments = <ButtonSegment<int>>[
      if (allRange < 4)
        ButtonSegment(value: allRange, label: const Text('ALL')),
      if (allRange >= 4) const ButtonSegment(value: 4, label: Text('1M')),
      if (allRange >= 8) const ButtonSegment(value: 8, label: Text('2M')),
      if (allRange >= 12) const ButtonSegment(value: 12, label: Text('3M')),
      if (allRange > 12)
        ButtonSegment(value: allRange, label: const Text('ALL')),
    ];
    final selectedRange =
        rangeSegments.any((segment) => segment.value == widget.rangeWeeks)
        ? widget.rangeWeeks
        : allRange;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DecoratedBox(
                decoration: BoxDecoration(
                  color: AppColors.charcoal.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Icon(
                    Icons.candlestick_chart_outlined,
                    color: AppColors.charcoal,
                    size: 22,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '종합 플로우 캔들',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 5),
                    Text(
                      widget.candles.isEmpty
                          ? '기간 데이터 없음'
                          : '${widget.candles.first.label} → ${widget.candles.last.label}',
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              FlowChip(
                label: change >= 0
                    ? '+${change.toStringAsFixed(1)}'
                    : change.toStringAsFixed(1),
                color: change >= 0 ? AppColors.green : AppColors.red,
              ),
            ],
          ),
          const SizedBox(height: 14),
          if (latest != null) ...[
            Row(
              children: [
                Expanded(
                  child: _ChartStat(
                    label: '종합',
                    value: latest.close.toStringAsFixed(1),
                    color: latest.close >= latest.open
                        ? AppColors.green
                        : AppColors.red,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _ChartStat(
                    label: '모멘텀',
                    value: latest.momentum.toStringAsFixed(0),
                    color: AppColors.blue,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _ChartStat(
                    label: '유동성',
                    value: latest.liquidity.toStringAsFixed(0),
                    color: AppColors.green,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _ChartStat(
                    label: '리스크',
                    value: latest.risk.toStringAsFixed(0),
                    color: latest.risk >= 60 ? AppColors.red : AppColors.amber,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),
          ],
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: SegmentedButton<int>(
              segments: rangeSegments,
              selected: {selectedRange},
              onSelectionChanged: (values) =>
                  widget.onRangeChanged(values.first),
            ),
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: 236,
            child: widget.candles.isEmpty
                ? const EmptyState(message: '캔들 데이터가 없습니다.')
                : LayoutBuilder(
                    builder: (context, constraints) {
                      final width = constraints.maxWidth <= 0
                          ? 1.0
                          : constraints.maxWidth;
                      return Listener(
                        behavior: HitTestBehavior.opaque,
                        onPointerDown: widget.totalCount <= 4
                            ? null
                            : (event) => _handleChartPointerDown(event, width),
                        onPointerMove: widget.totalCount <= 4
                            ? null
                            : (event) => _handleChartPointerMove(event, width),
                        onPointerUp: _handleChartPointerEnd,
                        onPointerCancel: _handleChartPointerEnd,
                        child: GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onDoubleTap: () =>
                              widget.onWindowChanged(const RangeValues(0, 1)),
                          child: FlowCompositeChart(candles: widget.candles),
                        ),
                      );
                    },
                  ),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: Text(
                  '세부 구간',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
              Text(
                '${widget.candles.length}/${widget.totalCount}',
                style: Theme.of(context).textTheme.labelLarge,
              ),
            ],
          ),
          RangeSlider(
            values: widget.detailWindow,
            min: 0,
            max: 1,
            divisions: widget.totalCount > 2 ? widget.totalCount - 1 : 1,
            labels: RangeLabels(
              '${(widget.detailWindow.start * 100).round()}%',
              '${(widget.detailWindow.end * 100).round()}%',
            ),
            onChanged: widget.totalCount > 2 ? widget.onWindowChanged : null,
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 10,
            runSpacing: 8,
            children: [
              _FlowLegendDot(label: '캔들: 종합지수', color: AppColors.green),
              _FlowLegendDot(label: '유동성', color: AppColors.muted),
              _FlowLegendDot(label: 'AI', color: AppColors.blue),
              _FlowLegendDot(label: 'BTC/ETH', color: AppColors.amber),
              _FlowLegendDot(label: '금', color: AppColors.charcoal),
              _FlowLegendDot(label: 'KOSPI', color: AppColors.green),
              _FlowLegendDot(label: '리스크', color: AppColors.red),
            ],
          ),
        ],
      ),
    );
  }
}

class _ChartStat extends StatelessWidget {
  const _ChartStat({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.canvas,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.line),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: 4),
            Text(
              value,
              style: Theme.of(
                context,
              ).textTheme.titleMedium?.copyWith(color: color),
            ),
          ],
        ),
      ),
    );
  }
}

class FlowCompositeChart extends StatelessWidget {
  const FlowCompositeChart({required this.candles, super.key});

  final List<FlowCandle> candles;

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: FlowCompositeChartPainter(candles: candles),
      size: const Size(double.infinity, 236),
    );
  }
}

class FlowCompositeChartPainter extends CustomPainter {
  const FlowCompositeChartPainter({required this.candles});

  final List<FlowCandle> candles;

  @override
  void paint(Canvas canvas, Size size) {
    if (candles.isEmpty || size.width <= 0 || size.height <= 0) {
      return;
    }

    final chartLeft = 42.0;
    final chart = Rect.fromLTWH(
      chartLeft,
      10,
      (size.width - chartLeft - 10).clamp(1.0, double.infinity).toDouble(),
      (size.height - 48).clamp(1.0, double.infinity).toDouble(),
    );
    var minValue = candles.first.low;
    var maxValue = candles.first.high;
    var maxLiquidity = candles.first.liquidity;
    for (final candle in candles) {
      for (final value in [
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.momentum,
        candle.risk,
        candle.aiFlow,
        candle.cryptoFlow,
        candle.goldFlow,
        candle.koreaFlow,
      ]) {
        if (value < minValue) {
          minValue = value;
        }
        if (value > maxValue) {
          maxValue = value;
        }
      }
      if (candle.liquidity > maxLiquidity) {
        maxLiquidity = candle.liquidity;
      }
    }

    final spread = maxValue - minValue;
    final padding = (spread * 0.14).clamp(3.0, 10.0).toDouble();
    var scaleMin = (minValue - padding).clamp(0.0, 100.0).toDouble();
    var scaleMax = (maxValue + padding).clamp(0.0, 100.0).toDouble();
    if (scaleMax - scaleMin < 8) {
      final midpoint = ((minValue + maxValue) / 2).clamp(4.0, 96.0).toDouble();
      scaleMin = (midpoint - 4).clamp(0.0, 100.0).toDouble();
      scaleMax = (midpoint + 4).clamp(0.0, 100.0).toDouble();
    }
    final scaleSpan = scaleMax - scaleMin <= 0 ? 1.0 : scaleMax - scaleMin;
    final volumeHeight = chart.height * 0.18;
    final volumeTop = chart.bottom - volumeHeight;
    final gridPaint = Paint()
      ..color = AppColors.line.withValues(alpha: 0.58)
      ..strokeWidth = 1;
    final axisPaint = Paint()
      ..color = AppColors.muted.withValues(alpha: 0.62)
      ..strokeWidth = 1;

    void drawText(
      String text,
      Offset offset, {
      Color? color,
      double fontSize = 10,
      TextAlign textAlign = TextAlign.left,
    }) {
      final effectiveColor = color ?? AppColors.muted;
      final painter = TextPainter(
        text: TextSpan(
          text: text,
          style: TextStyle(
            color: effectiveColor,
            fontSize: fontSize,
            fontWeight: FontWeight.w700,
            height: 1,
          ),
        ),
        textDirection: TextDirection.ltr,
        textAlign: textAlign,
      )..layout();
      var dx = offset.dx;
      if (textAlign == TextAlign.center) {
        dx -= painter.width / 2;
      } else if (textAlign == TextAlign.right) {
        dx -= painter.width;
      }
      painter.paint(canvas, Offset(dx, offset.dy));
    }

    String formatAxisValue(double value) {
      return value.abs() >= 10
          ? value.toStringAsFixed(0)
          : value.toStringAsFixed(1);
    }

    final step = candles.length == 1
        ? chart.width
        : chart.width / candles.length;
    final candleWidth = (step * 0.48).clamp(3.0, 15.0).toDouble();

    double yFor(double value) {
      final normalized = ((value - scaleMin) / scaleSpan)
          .clamp(0.0, 1.0)
          .toDouble();
      return chart.bottom - normalized * chart.height;
    }

    double xForIndex(int index) {
      return chart.left + step * index + step / 2;
    }

    for (var i = 0; i <= 4; i++) {
      final value = scaleMax - scaleSpan * i / 4;
      final y = yFor(value);
      canvas.drawLine(Offset(chart.left, y), Offset(chart.right, y), gridPaint);
      drawText(
        formatAxisValue(value),
        Offset(chart.left - 8, y - 5),
        textAlign: TextAlign.right,
      );
    }

    final tickCount = candles.length <= 5 ? candles.length : 5;
    final tickIndexes = <int>{};
    for (var i = 0; i < tickCount; i++) {
      final index = tickCount == 1
          ? 0
          : ((candles.length - 1) * i / (tickCount - 1)).round();
      if (!tickIndexes.add(index)) {
        continue;
      }
      final x = xForIndex(index);
      canvas.drawLine(
        Offset(x, chart.top),
        Offset(x, chart.bottom),
        Paint()
          ..color = AppColors.line.withValues(alpha: i == 0 ? 0.62 : 0.34)
          ..strokeWidth = 1,
      );
    }
    canvas.drawLine(
      Offset(chart.left, chart.bottom),
      Offset(chart.right, chart.bottom),
      axisPaint,
    );
    canvas.drawLine(
      Offset(chart.left, chart.top),
      Offset(chart.left, chart.bottom),
      axisPaint,
    );
    canvas.drawLine(
      Offset(chart.left, volumeTop),
      Offset(chart.right, volumeTop),
      Paint()
        ..color = AppColors.line.withValues(alpha: 0.36)
        ..strokeWidth = 1,
    );

    canvas.save();
    canvas.clipRect(chart);
    for (var i = 0; i < candles.length; i++) {
      final candle = candles[i];
      final centerX = xForIndex(i);
      final isUp = candle.close >= candle.open;
      final color = isUp ? AppColors.green : AppColors.red;
      final wickPaint = Paint()
        ..color = color
        ..strokeWidth = 1.4
        ..strokeCap = StrokeCap.round;
      final bodyPaint = Paint()..color = color.withValues(alpha: 0.78);
      final volumePaint = Paint()..color = color.withValues(alpha: 0.16);
      final liquidityRatio = maxLiquidity <= 0
          ? 0.0
          : (candle.liquidity / maxLiquidity).clamp(0.0, 1.0).toDouble();

      canvas.drawRect(
        Rect.fromLTWH(
          centerX - candleWidth / 2,
          volumeTop + volumeHeight * (1 - liquidityRatio),
          candleWidth,
          volumeHeight * liquidityRatio,
        ),
        volumePaint,
      );

      canvas.drawLine(
        Offset(centerX, yFor(candle.high)),
        Offset(centerX, yFor(candle.low)),
        wickPaint,
      );
      final top = yFor(candle.open > candle.close ? candle.open : candle.close);
      final bottom = yFor(
        candle.open < candle.close ? candle.open : candle.close,
      );
      final bodyMiddle = (top + bottom) / 2;
      final bodyTop = bottom - top < 2.5
          ? (bodyMiddle - 1.25).clamp(chart.top, chart.bottom).toDouble()
          : top;
      final bodyBottom = bottom - top < 2.5
          ? (bodyMiddle + 1.25).clamp(chart.top, chart.bottom).toDouble()
          : bottom;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTRB(
            centerX - candleWidth / 2,
            bodyTop,
            centerX + candleWidth / 2,
            bodyBottom,
          ),
          const Radius.circular(2.5),
        ),
        bodyPaint,
      );
    }

    void drawLine(
      double Function(FlowCandle candle) selector,
      Color color, {
      double alpha = 0.78,
    }) {
      if (candles.length < 2) {
        return;
      }
      final points = <Offset>[];
      for (var i = 0; i < candles.length; i++) {
        points.add(Offset(xForIndex(i), yFor(selector(candles[i]))));
      }
      canvas.drawPath(
        _smoothChartPath(points),
        Paint()
          ..color = color.withValues(alpha: alpha)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.2
          ..strokeCap = StrokeCap.round
          ..strokeJoin = StrokeJoin.round,
      );
    }

    drawLine((candle) => candle.aiFlow, AppColors.blue);
    drawLine((candle) => candle.cryptoFlow, AppColors.amber);
    drawLine((candle) => candle.goldFlow, AppColors.charcoal, alpha: 0.72);
    drawLine((candle) => candle.koreaFlow, AppColors.green);
    drawLine((candle) => candle.risk, AppColors.red, alpha: 0.66);

    final last = candles.last;
    final lastX = xForIndex(candles.length - 1);
    final lastY = yFor(last.close);
    final lastColor = last.close >= last.open ? AppColors.green : AppColors.red;
    final markerPaint = Paint()
      ..color = lastColor
      ..style = PaintingStyle.fill;
    final markerLinePaint = Paint()
      ..color = lastColor.withValues(alpha: 0.34)
      ..strokeWidth = 1;
    canvas.drawLine(
      Offset(chart.left, lastY),
      Offset(chart.right, lastY),
      markerLinePaint,
    );
    canvas.drawCircle(
      Offset(lastX, lastY),
      7,
      Paint()..color = lastColor.withValues(alpha: 0.16),
    );
    canvas.drawCircle(Offset(lastX, lastY), 4, markerPaint);
    canvas.restore();

    final priceText = last.close.toStringAsFixed(1);
    final pricePainter = TextPainter(
      text: TextSpan(
        text: priceText,
        style: TextStyle(
          color: lastColor,
          fontSize: 10,
          fontWeight: FontWeight.w800,
          height: 1,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final pricePillWidth = pricePainter.width + 12;
    final pricePillHeight = 20.0;
    final pricePillLeft = chart.width > pricePillWidth + 6
        ? chart.right - pricePillWidth - 2
        : chart.left + 2;
    final pricePillTop = (lastY - pricePillHeight / 2)
        .clamp(chart.top + 2, chart.bottom - pricePillHeight - 2)
        .toDouble();
    final pricePill = RRect.fromRectAndRadius(
      Rect.fromLTWH(
        pricePillLeft,
        pricePillTop,
        pricePillWidth,
        pricePillHeight,
      ),
      const Radius.circular(6),
    );
    canvas.drawRRect(
      pricePill,
      Paint()..color = lastColor.withValues(alpha: 0.12),
    );
    canvas.drawRRect(
      pricePill,
      Paint()
        ..color = lastColor.withValues(alpha: 0.36)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1,
    );
    pricePainter.paint(canvas, Offset(pricePill.left + 6, pricePill.top + 5));

    if (candles.length > 1) {
      for (final index in tickIndexes) {
        final x = xForIndex(index).clamp(chart.left + 12, chart.right - 12);
        drawText(
          candles[index].label,
          Offset(x.toDouble(), chart.bottom + 8),
          textAlign: TextAlign.center,
        );
      }
    }
  }

  Path _smoothChartPath(List<Offset> points) {
    final path = Path()..moveTo(points.first.dx, points.first.dy);
    if (points.length == 2) {
      path.lineTo(points.last.dx, points.last.dy);
      return path;
    }
    for (var i = 1; i < points.length - 1; i++) {
      final current = points[i];
      final next = points[i + 1];
      final midpoint = Offset(
        (current.dx + next.dx) / 2,
        (current.dy + next.dy) / 2,
      );
      path.quadraticBezierTo(current.dx, current.dy, midpoint.dx, midpoint.dy);
    }
    path.lineTo(points.last.dx, points.last.dy);
    return path;
  }

  @override
  bool shouldRepaint(covariant FlowCompositeChartPainter oldDelegate) {
    return oldDelegate.candles != candles;
  }
}

class _FlowLegendDot extends StatelessWidget {
  const _FlowLegendDot({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        DecoratedBox(
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          child: const SizedBox(width: 8, height: 8),
        ),
        const SizedBox(width: 5),
        Text(label, style: Theme.of(context).textTheme.bodyMedium),
      ],
    );
  }
}

class CapitalFlowCard extends StatelessWidget {
  const CapitalFlowCard({required this.flow, super.key});

  final CapitalFlow flow;

  @override
  Widget build(BuildContext context) {
    final color = scoreColor(flow.flowScore);
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DecoratedBox(
                decoration: BoxDecoration(
                  color: _assetClassColor(
                    flow.assetClass,
                  ).withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Icon(
                    _assetClassIcon(flow.assetClass),
                    color: _assetClassColor(flow.assetClass),
                    size: 22,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      flow.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 5),
                    Text(
                      '${flow.regionLabel} → ${flow.destination}',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              _ScoreDial(score: flow.flowScore, color: color),
            ],
          ),
          const SizedBox(height: 14),
          SizedBox(
            height: 66,
            child: Sparkline(values: flow.trend, color: color),
          ),
          const SizedBox(height: 14),
          Text(flow.thesis, style: Theme.of(context).textTheme.bodyLarge),
          const SizedBox(height: 14),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FlowChip(label: flow.assetClass.label, color: AppColors.blue),
              FlowChip(label: flow.netFlowLabel, color: AppColors.charcoal),
              FlowChip(label: flow.updatedLabel, color: AppColors.muted),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: _MiniStat(
                  label: '모멘텀',
                  value: flow.momentum,
                  color: AppColors.green,
                ),
              ),
              Expanded(
                child: _MiniStat(
                  label: '유동성',
                  value: flow.liquidity,
                  color: AppColors.blue,
                ),
              ),
              Expanded(
                child: _MiniStat(
                  label: '위험',
                  value: flow.risk,
                  color: flow.risk >= 60 ? AppColors.red : AppColors.amber,
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          Text(flow.signal, style: Theme.of(context).textTheme.labelLarge),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final driver in flow.drivers)
                FlowChip(label: driver, color: AppColors.charcoal),
            ],
          ),
        ],
      ),
    );
  }
}

class DataApiSourceCard extends StatelessWidget {
  const DataApiSourceCard({required this.api, super.key});

  final DataApiSource api;

  @override
  Widget build(BuildContext context) {
    final color = _apiStatusColor(api.status);
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DecoratedBox(
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Icon(
                    _apiStatusIcon(api.status),
                    color: color,
                    size: 22,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      api.name,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 5),
                    Text(
                      api.provider,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              FlowChip(label: api.status.label, color: color),
            ],
          ),
          const SizedBox(height: 14),
          Text(api.coverage, style: Theme.of(context).textTheme.bodyLarge),
          const SizedBox(height: 10),
          Text(
            '사용 화면: ${api.usedFor}',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FlowChip(label: api.keyName, color: AppColors.charcoal),
              FlowChip(label: api.docsUrl, color: AppColors.blue),
            ],
          ),
        ],
      ),
    );
  }
}

class DataApiKeyField extends StatelessWidget {
  const DataApiKeyField({
    required this.api,
    required this.controller,
    required this.enabled,
    required this.obscureText,
    required this.hasKey,
    required this.onChanged,
    super.key,
  });

  final DataApiSource api;
  final TextEditingController controller;
  final bool enabled;
  final bool obscureText;
  final bool hasKey;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final color = _apiStatusColor(api.status);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            DecoratedBox(
              decoration: BoxDecoration(
                color: color.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Padding(
                padding: const EdgeInsets.all(9),
                child: Icon(_apiStatusIcon(api.status), color: color, size: 20),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    api.name,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    api.provider,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            FlowChip(
              label: hasKey ? 'key 저장됨' : 'key 필요',
              color: hasKey ? AppColors.green : AppColors.amber,
            ),
          ],
        ),
        const SizedBox(height: 12),
        Text(api.coverage, style: Theme.of(context).textTheme.bodyLarge),
        const SizedBox(height: 8),
        Text(
          '사용 화면: ${api.usedFor}',
          style: Theme.of(context).textTheme.bodyMedium,
        ),
        const SizedBox(height: 8),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.key_outlined, size: 16, color: AppColors.muted),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                '발급 위치: ${api.docsUrl}',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            FlowChip(label: api.status.label, color: color),
            FlowChip(label: '읽기 전용', color: AppColors.blue),
            FlowChip(label: api.keyName, color: AppColors.charcoal),
            FlowChip(label: api.docsUrl, color: AppColors.blue),
          ],
        ),
        const SizedBox(height: 12),
        TextField(
          controller: controller,
          enabled: enabled,
          obscureText: obscureText,
          keyboardType: TextInputType.visiblePassword,
          decoration: InputDecoration(
            labelText: 'API key',
            hintText: api.keyName,
            border: const OutlineInputBorder(),
          ),
          onChanged: (_) => onChanged(),
        ),
      ],
    );
  }
}

class EmergingFlowCard extends StatelessWidget {
  const EmergingFlowCard({required this.flow, super.key});

  final EmergingCapitalFlow flow;

  @override
  Widget build(BuildContext context) {
    final color = scoreColor(flow.probability);
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DecoratedBox(
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Icon(Icons.route_outlined, color: color, size: 22),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      flow.title,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 6),
                    Text(
                      '${flow.from} → ${flow.to}',
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              FlowChip(label: '${flow.probability}%', color: color),
            ],
          ),
          const SizedBox(height: 14),
          Text(flow.trigger, style: Theme.of(context).textTheme.bodyLarge),
          const SizedBox(height: 12),
          FactorBar(label: '현실화 가능성', value: flow.probability, color: color),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FlowChip(label: flow.timeframe, color: AppColors.blue),
              for (final item in flow.beneficiaries)
                FlowChip(label: item, color: AppColors.green),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            '관찰 지표: ${flow.watch}',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 8),
          Text(
            '리스크: ${flow.risks.join(' · ')}',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ],
      ),
    );
  }
}

class ApiStatusCard extends StatelessWidget {
  const ApiStatusCard({
    required this.snapshot,
    required this.liveQuoteCount,
    required this.onRefresh,
    super.key,
  });

  final QuoteApiSnapshot snapshot;
  final int liveQuoteCount;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final color = switch (snapshot.status) {
      QuoteFetchStatus.ready => AppColors.green,
      QuoteFetchStatus.partial => AppColors.amber,
      QuoteFetchStatus.loading => AppColors.blue,
      QuoteFetchStatus.missingApiKey => AppColors.amber,
      QuoteFetchStatus.failed => AppColors.red,
      QuoteFetchStatus.idle => AppColors.muted,
    };
    final updatedAt = snapshot.updatedAt;

    return AppCard(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          DecoratedBox(
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Padding(
              padding: const EdgeInsets.all(10),
              child: Icon(Icons.cloud_sync_outlined, color: color, size: 22),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        snapshot.statusLabel,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  snapshot.message,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    FlowChip(
                      label: '${snapshot.provider} ${snapshot.endpoint}',
                      color: AppColors.blue,
                    ),
                    FlowChip(
                      label:
                          'API ${snapshot.apiKeyConfigured ? '연결' : 'key 필요'}',
                      color: snapshot.apiKeyConfigured
                          ? AppColors.green
                          : AppColors.amber,
                    ),
                    FlowChip(
                      label:
                          'live $liveQuoteCount/${snapshot.requestedSymbols}',
                      color: liveQuoteCount > 0
                          ? AppColors.green
                          : AppColors.muted,
                    ),
                    if (updatedAt != null)
                      FlowChip(
                        label: _formatClock(updatedAt),
                        color: AppColors.charcoal,
                      ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          IconButton(
            tooltip: '시세 새로고침',
            onPressed: snapshot.status == QuoteFetchStatus.loading
                ? null
                : onRefresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
    );
  }
}

class MarketPulseCard extends StatelessWidget {
  const MarketPulseCard({required this.pulse, super.key});

  final MarketPulse pulse;

  @override
  Widget build(BuildContext context) {
    final score = pulse.score.clamp(0, 100);
    final color = scoreColor(score);
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      pulse.title,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 6),
                    Text(
                      pulse.bias,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              _ScoreDial(score: score, color: color),
            ],
          ),
          const SizedBox(height: 16),
          SizedBox(
            height: 72,
            child: Sparkline(values: pulse.heat, color: color),
          ),
          const SizedBox(height: 14),
          Text(pulse.summary, style: Theme.of(context).textTheme.bodyLarge),
          const SizedBox(height: 14),
          Row(
            children: [
              FlowChip(
                label: pulse.change >= 0
                    ? '+${pulse.change.toStringAsFixed(1)}%'
                    : '${pulse.change.toStringAsFixed(1)}%',
                color: pulse.change >= 0 ? AppColors.green : AppColors.red,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '${pulse.netFlow} · ${pulse.updatedLabel}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.right,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class ThemeBoardScreen extends StatelessWidget {
  const ThemeBoardScreen({required this.themes, super.key});

  final List<ThemePulse> themes;

  @override
  Widget build(BuildContext context) {
    if (themes.isEmpty) {
      return ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
        children: const [EmptyState(message: '선택한 시장의 테마 데이터가 없습니다.')],
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
      itemCount: themes.length + 1,
      separatorBuilder: (_, index) => SizedBox(height: index == 0 ? 10 : 12),
      itemBuilder: (context, index) {
        if (index == 0) {
          return const SectionHeader(title: '테마 맵');
        }
        return ThemePulseCard(theme: themes[index - 1]);
      },
    );
  }
}

class ThemePulseCard extends StatelessWidget {
  const ThemePulseCard({required this.theme, this.compact = false, super.key});

  final ThemePulse theme;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final color = scoreColor(theme.score);
    return AppCard(
      padding: EdgeInsets.all(compact ? 14 : 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  theme.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
              FlowChip(label: theme.stage.label, color: color),
            ],
          ),
          const SizedBox(height: 10),
          if (!compact)
            Text(theme.narrative, style: Theme.of(context).textTheme.bodyLarge),
          if (!compact) const SizedBox(height: 14),
          SizedBox(
            height: compact ? 52 : 66,
            child: Sparkline(values: theme.trend, color: color),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: _MiniStat(label: '흐름', value: theme.score, color: color),
              ),
              Expanded(
                child: _MiniStat(
                  label: '확산',
                  value: theme.diffusion,
                  color: AppColors.blue,
                ),
              ),
              Expanded(
                child: _MiniStat(
                  label: '위험',
                  value: theme.risk,
                  color: theme.risk >= 55 ? AppColors.red : AppColors.amber,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final leader in theme.leaders)
                FlowChip(label: leader, color: AppColors.charcoal),
            ],
          ),
        ],
      ),
    );
  }
}

class WatchlistScreen extends StatelessWidget {
  const WatchlistScreen({
    required this.equities,
    required this.quotes,
    super.key,
  });

  final List<EquityFlow> equities;
  final Map<String, LiveQuote> quotes;

  @override
  Widget build(BuildContext context) {
    if (equities.isEmpty) {
      return ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
        children: const [EmptyState(message: '선택한 시장의 관심 종목이 없습니다.')],
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
      itemCount: equities.length + 1,
      separatorBuilder: (_, index) => SizedBox(height: index == 0 ? 10 : 10),
      itemBuilder: (context, index) {
        if (index == 0) {
          return const SectionHeader(title: '관심 종목');
        }
        return EquityFlowTile(
          equity: equities[index - 1],
          quote: quotes[equities[index - 1].symbol],
          onTap: () => showModalBottomSheet<void>(
            context: context,
            isScrollControlled: true,
            showDragHandle: true,
            builder: (_) => StockDetailSheet(
              equity: equities[index - 1],
              quote: quotes[equities[index - 1].symbol],
            ),
          ),
        );
      },
    );
  }
}

class EquityFlowTile extends StatelessWidget {
  const EquityFlowTile({
    required this.equity,
    this.quote,
    this.onTap,
    super.key,
  });

  final EquityFlow equity;
  final LiveQuote? quote;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final color = scoreColor(equity.flowScore);
    final effectiveChange = quote?.changePercent ?? equity.changePercent;
    final effectivePrice =
        quote?.priceLabel(equity.region) ?? equity.priceLabel;
    final changeColor = effectiveChange >= 0 ? AppColors.green : AppColors.red;

    return AppCard(
      onTap: onTap,
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _SymbolAvatar(symbol: equity.symbol, color: color),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${equity.name} · ${equity.symbol}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${equity.theme} · ${equity.stage.label}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    '${equity.flowScore}',
                    style: Theme.of(
                      context,
                    ).textTheme.titleLarge?.copyWith(color: color),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    effectiveChange >= 0
                        ? '+${effectiveChange.toStringAsFixed(2)}%'
                        : '${effectiveChange.toStringAsFixed(2)}%',
                    style: TextStyle(
                      color: changeColor,
                      fontWeight: FontWeight.w800,
                      fontSize: 12,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: 56,
            child: Sparkline(values: equity.sparkline, color: color),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Text(
                effectivePrice,
                style: Theme.of(context).textTheme.labelLarge,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Wrap(
                  alignment: WrapAlignment.end,
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    for (final tag in equity.tags.take(2))
                      FlowChip(label: tag, color: AppColors.blue),
                    FlowChip(
                      label: quote == null
                          ? 'mock'
                          : 'API ${quote!.latestTradingDay}',
                      color: quote == null ? AppColors.amber : AppColors.green,
                    ),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class StockDetailSheet extends StatelessWidget {
  const StockDetailSheet({required this.equity, this.quote, super.key});

  final EquityFlow equity;
  final LiveQuote? quote;

  @override
  Widget build(BuildContext context) {
    final color = scoreColor(equity.flowScore);
    final effectivePrice =
        quote?.priceLabel(equity.region) ?? equity.priceLabel;
    final effectiveChange = quote?.changePercent ?? equity.changePercent;
    return SafeArea(
      top: false,
      child: SingleChildScrollView(
        padding: EdgeInsets.fromLTRB(
          20,
          4,
          20,
          20 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                _SymbolAvatar(symbol: equity.symbol, color: color, size: 48),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        equity.name,
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '${equity.symbol} · ${equity.theme}',
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                    ],
                  ),
                ),
                FlowChip(label: '${equity.flowScore}', color: color),
              ],
            ),
            const SizedBox(height: 18),
            SizedBox(
              height: 100,
              child: Sparkline(values: equity.sparkline, color: color),
            ),
            const SizedBox(height: 14),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FlowChip(label: effectivePrice, color: AppColors.charcoal),
                FlowChip(
                  label: effectiveChange >= 0
                      ? '+${effectiveChange.toStringAsFixed(2)}%'
                      : '${effectiveChange.toStringAsFixed(2)}%',
                  color: effectiveChange >= 0 ? AppColors.green : AppColors.red,
                ),
                FlowChip(
                  label: quote == null
                      ? 'mock 데이터'
                      : '${quote!.provider} ${quote!.latestTradingDay}',
                  color: quote == null ? AppColors.amber : AppColors.green,
                ),
              ],
            ),
            const SizedBox(height: 18),
            Text(equity.thesis, style: Theme.of(context).textTheme.bodyLarge),
            const SizedBox(height: 18),
            FactorBar(
              label: '상대강도',
              value: equity.relativeStrength,
              color: AppColors.green,
            ),
            const SizedBox(height: 14),
            FactorBar(
              label: '거래량 가속',
              value: equity.volumeSurge,
              color: AppColors.blue,
            ),
            const SizedBox(height: 14),
            FactorBar(
              label: '위험도',
              value: equity.risk,
              color: equity.risk >= 55 ? AppColors.red : AppColors.amber,
            ),
            const SizedBox(height: 18),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FlowChip(label: equity.stage.label, color: color),
                for (final tag in equity.tags)
                  FlowChip(label: tag, color: AppColors.charcoal),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class InvestmentChecklistScreen extends StatefulWidget {
  const InvestmentChecklistScreen({
    required this.day,
    required this.selectedDate,
    required this.focusedMonth,
    required this.monthDays,
    required this.loaded,
    required this.pulses,
    required this.capitalFlows,
    required this.themes,
    required this.equities,
    required this.quotes,
    required this.quoteSnapshot,
    required this.onRefreshQuotes,
    required this.onDateSelected,
    required this.onMonthChanged,
    required this.onTodaySelected,
    required this.onItemChanged,
    required this.onAddItem,
    required this.onRemoveItem,
    required this.onSaveNote,
    required this.onResetDay,
    super.key,
  });

  final InvestmentChecklistDay day;
  final DateTime selectedDate;
  final DateTime focusedMonth;
  final Map<String, InvestmentChecklistDay> monthDays;
  final bool loaded;
  final List<MarketPulse> pulses;
  final List<CapitalFlow> capitalFlows;
  final List<ThemePulse> themes;
  final List<EquityFlow> equities;
  final Map<String, LiveQuote> quotes;
  final QuoteApiSnapshot quoteSnapshot;
  final VoidCallback onRefreshQuotes;
  final ValueChanged<DateTime> onDateSelected;
  final ValueChanged<DateTime> onMonthChanged;
  final VoidCallback onTodaySelected;
  final Future<void> Function(String itemId, bool checked) onItemChanged;
  final Future<void> Function(String label) onAddItem;
  final Future<void> Function(String itemId) onRemoveItem;
  final Future<void> Function(String note) onSaveNote;
  final Future<void> Function() onResetDay;

  @override
  State<InvestmentChecklistScreen> createState() =>
      _InvestmentChecklistScreenState();
}

class _InvestmentChecklistScreenState extends State<InvestmentChecklistScreen> {
  final _newItemController = TextEditingController();
  final _noteController = TextEditingController();
  bool _savingNote = false;
  bool _addingItem = false;
  bool _resettingDay = false;

  @override
  void initState() {
    super.initState();
    _noteController.text = widget.day.note;
  }

  @override
  void didUpdateWidget(covariant InvestmentChecklistScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.day.dateKey != widget.day.dateKey ||
        oldWidget.day.note != widget.day.note) {
      _noteController.text = widget.day.note;
    }
  }

  @override
  void dispose() {
    _newItemController.dispose();
    _noteController.dispose();
    super.dispose();
  }

  Future<void> _addItem() async {
    final label = _newItemController.text.trim();
    if (label.isEmpty) {
      return;
    }
    setState(() => _addingItem = true);
    await widget.onAddItem(label);
    if (!mounted) {
      return;
    }
    _newItemController.clear();
    setState(() => _addingItem = false);
  }

  Future<void> _saveNote() async {
    setState(() => _savingNote = true);
    await widget.onSaveNote(_noteController.text);
    if (!mounted) {
      return;
    }
    setState(() => _savingNote = false);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('체크 메모 저장됨')));
  }

  Future<void> _resetDay() async {
    setState(() => _resettingDay = true);
    await widget.onResetDay();
    if (!mounted) {
      return;
    }
    setState(() => _resettingDay = false);
  }

  @override
  Widget build(BuildContext context) {
    final day = widget.day;
    final completedRate = (day.completionRate * 100).round();
    final activeDays = widget.monthDays.values
        .where((day) => day.hasActivity || day.isComplete)
        .length;
    final completeDays = widget.monthDays.values
        .where((day) => day.isComplete)
        .length;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
      children: [
        Row(
          children: [
            MetricPill(
              icon: Icons.task_alt,
              label: '완료율',
              value: '$completedRate%',
              color: day.isComplete ? AppColors.green : AppColors.amber,
            ),
            const SizedBox(width: 10),
            MetricPill(
              icon: Icons.pending_actions_outlined,
              label: '남은 체크',
              value: '${day.remainingCount}',
              color: day.remainingCount == 0 ? AppColors.green : AppColors.red,
            ),
            const SizedBox(width: 10),
            MetricPill(
              icon: Icons.calendar_month_outlined,
              label: '이번 달',
              value: '$completeDays/$activeDays',
              color: AppColors.blue,
            ),
          ],
        ),
        const SizedBox(height: 18),
        ChecklistDataPanel(
          pulses: widget.pulses,
          capitalFlows: widget.capitalFlows,
          themes: widget.themes,
          equities: widget.equities,
          quotes: widget.quotes,
          quoteSnapshot: widget.quoteSnapshot,
          onRefreshQuotes: widget.onRefreshQuotes,
        ),
        const SizedBox(height: 12),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: AppColors.blue.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(10),
                      child: Icon(
                        Icons.calendar_month_outlined,
                        color: AppColors.blue,
                        size: 22,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      '체크 캘린더',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  FlowChip(
                    label: _formatChecklistMonth(widget.focusedMonth),
                    color: AppColors.charcoal,
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  IconButton(
                    tooltip: '이전 달',
                    onPressed: widget.loaded
                        ? () => widget.onMonthChanged(
                            _shiftChecklistMonth(widget.focusedMonth, -1),
                          )
                        : null,
                    icon: const Icon(Icons.chevron_left),
                  ),
                  Expanded(
                    child: Center(
                      child: Text(
                        _formatChecklistMonth(widget.focusedMonth),
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                    ),
                  ),
                  TextButton.icon(
                    onPressed: widget.loaded ? widget.onTodaySelected : null,
                    icon: const Icon(Icons.today_outlined, size: 18),
                    label: const Text('오늘'),
                  ),
                  IconButton(
                    tooltip: '다음 달',
                    onPressed: widget.loaded
                        ? () => widget.onMonthChanged(
                            _shiftChecklistMonth(widget.focusedMonth, 1),
                          )
                        : null,
                    icon: const Icon(Icons.chevron_right),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  for (final label in const ['월', '화', '수', '목', '금', '토', '일'])
                    Expanded(
                      child: Center(
                        child: Text(
                          label,
                          style: Theme.of(context).textTheme.labelMedium,
                        ),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              GridView.count(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                crossAxisCount: 7,
                childAspectRatio: 0.88,
                mainAxisSpacing: 6,
                crossAxisSpacing: 6,
                children: [
                  for (final date in _calendarDates(widget.focusedMonth))
                    _ChecklistCalendarDayCell(
                      date: date,
                      day: date == null
                          ? null
                          : _dayForDate(date, widget.day, widget.monthDays),
                      selectedDate: widget.selectedDate,
                      onSelected: widget.loaded && date != null
                          ? () => widget.onDateSelected(date)
                          : null,
                    ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color:
                          (day.isComplete ? AppColors.green : AppColors.amber)
                              .withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(10),
                      child: Icon(
                        day.isComplete
                            ? Icons.verified_outlined
                            : Icons.fact_check_outlined,
                        color: day.isComplete
                            ? AppColors.green
                            : AppColors.amber,
                        size: 22,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '오늘 투자 전 체크',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          _formatChecklistDate(widget.selectedDate),
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ],
                    ),
                  ),
                  FlowChip(
                    label: day.isComplete ? '투자 가능' : '보류',
                    color: day.isComplete ? AppColors.green : AppColors.red,
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(
                    child: LinearProgressIndicator(
                      value: day.completionRate,
                      minHeight: 8,
                      borderRadius: BorderRadius.circular(8),
                      backgroundColor: AppColors.line,
                      valueColor: AlwaysStoppedAnimation<Color>(
                        day.isComplete ? AppColors.green : AppColors.amber,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Text(
                    '${day.completedCount}/${day.totalCount} 완료',
                    style: Theme.of(context).textTheme.labelLarge,
                  ),
                ],
              ),
              const SizedBox(height: 12),
              for (final item in day.items)
                _ChecklistItemRow(
                  item: item,
                  enabled: widget.loaded,
                  onChanged: (checked) =>
                      widget.onItemChanged(item.id, checked),
                  onRemove: item.isCustom
                      ? () => widget.onRemoveItem(item.id)
                      : null,
                ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _newItemController,
                      enabled: widget.loaded && !_addingItem,
                      textInputAction: TextInputAction.done,
                      decoration: const InputDecoration(
                        labelText: '체크 항목 추가',
                        border: OutlineInputBorder(),
                      ),
                      onSubmitted: (_) => _addItem(),
                    ),
                  ),
                  const SizedBox(width: 10),
                  IconButton.filled(
                    tooltip: '체크 항목 추가',
                    onPressed: widget.loaded && !_addingItem ? _addItem : null,
                    icon: _addingItem
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.add),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _noteController,
                enabled: widget.loaded && !_savingNote,
                maxLines: 3,
                decoration: const InputDecoration(
                  labelText: '오늘 메모',
                  hintText: '체크 후 남길 리스크, 시나리오, 보류 사유',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: widget.loaded && !_resettingDay
                          ? _resetDay
                          : null,
                      icon: _resettingDay
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.restart_alt),
                      label: const Text('선택일 초기화'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: widget.loaded && !_savingNote
                          ? _saveNote
                          : null,
                      icon: _savingNote
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.save_outlined),
                      label: const Text('메모 저장'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }

  InvestmentChecklistDay? _dayForDate(
    DateTime date,
    InvestmentChecklistDay selectedDay,
    Map<String, InvestmentChecklistDay> monthDays,
  ) {
    final dateKey = checklistDateKey(date);
    if (dateKey == selectedDay.dateKey) {
      return selectedDay;
    }
    return monthDays[dateKey];
  }
}

class ChecklistDataPanel extends StatelessWidget {
  const ChecklistDataPanel({
    required this.pulses,
    required this.capitalFlows,
    required this.themes,
    required this.equities,
    required this.quotes,
    required this.quoteSnapshot,
    required this.onRefreshQuotes,
    super.key,
  });

  final List<MarketPulse> pulses;
  final List<CapitalFlow> capitalFlows;
  final List<ThemePulse> themes;
  final List<EquityFlow> equities;
  final Map<String, LiveQuote> quotes;
  final QuoteApiSnapshot quoteSnapshot;
  final VoidCallback onRefreshQuotes;

  @override
  Widget build(BuildContext context) {
    final rankedPulses = pulses.toList(growable: false)
      ..sort((a, b) => b.score.compareTo(a.score));
    final rankedFlows = capitalFlows.toList(growable: false)
      ..sort((a, b) => b.flowScore.compareTo(a.flowScore));
    final rankedThemes = themes.toList(growable: false)
      ..sort((a, b) => b.score.compareTo(a.score));
    final rankedEquities = equities.toList(growable: false)
      ..sort((a, b) => b.flowScore.compareTo(a.flowScore));
    final topPulse = rankedPulses.isEmpty ? null : rankedPulses.first;
    final topFlow = rankedFlows.isEmpty ? null : rankedFlows.first;
    final topTheme = rankedThemes.isEmpty ? null : rankedThemes.first;
    final highRiskCount = equities.where((equity) => equity.risk >= 60).length;
    final apiColor = switch (quoteSnapshot.status) {
      QuoteFetchStatus.ready => AppColors.green,
      QuoteFetchStatus.partial => AppColors.amber,
      QuoteFetchStatus.loading => AppColors.blue,
      QuoteFetchStatus.missingApiKey => AppColors.amber,
      QuoteFetchStatus.failed => AppColors.red,
      QuoteFetchStatus.idle => AppColors.muted,
    };

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DecoratedBox(
                decoration: BoxDecoration(
                  color: AppColors.green.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Icon(
                    Icons.query_stats_outlined,
                    color: AppColors.green,
                    size: 22,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '오늘 데이터 확인',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '체크 전 시장, 자금, 관심 종목 데이터를 바로 확인',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: '시세 새로고침',
                onPressed: quoteSnapshot.status == QuoteFetchStatus.loading
                    ? null
                    : onRefreshQuotes,
                icon: quoteSnapshot.status == QuoteFetchStatus.loading
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh),
              ),
            ],
          ),
          const SizedBox(height: 14),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FlowChip(label: quoteSnapshot.statusLabel, color: apiColor),
              FlowChip(
                label: '${quoteSnapshot.provider} ${quoteSnapshot.endpoint}',
                color: AppColors.blue,
              ),
              FlowChip(
                label:
                    'live ${quotes.length}/${quoteSnapshot.requestedSymbols}',
                color: quotes.isEmpty ? AppColors.muted : AppColors.green,
              ),
              if (highRiskCount > 0)
                FlowChip(label: '고위험 $highRiskCount', color: AppColors.red),
            ],
          ),
          const SizedBox(height: 14),
          if (topPulse == null && topFlow == null && rankedEquities.isEmpty)
            Row(
              children: [
                Icon(Icons.info_outline, color: AppColors.muted),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    '체크 화면에 연결할 데이터가 없습니다.',
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                ),
              ],
            )
          else ...[
            if (topPulse != null)
              _ChecklistDataLine(
                icon: Icons.public_outlined,
                title: '시장 펄스',
                value: '${topPulse.title} · ${topPulse.score}',
                detail:
                    '${topPulse.bias} · ${topPulse.netFlow} · ${topPulse.updatedLabel}',
                color: scoreColor(topPulse.score),
              ),
            if (topFlow != null) ...[
              const Divider(height: 20),
              _ChecklistDataLine(
                icon: _assetClassIcon(topFlow.assetClass),
                title: '자금 흐름',
                value: '${topFlow.name} · ${topFlow.flowScore}',
                detail:
                    '${topFlow.regionLabel} → ${topFlow.destination} · ${topFlow.netFlowLabel}',
                color: _assetClassColor(topFlow.assetClass),
              ),
            ],
            if (topTheme != null) ...[
              const Divider(height: 20),
              _ChecklistDataLine(
                icon: Icons.bubble_chart_outlined,
                title: '강세 테마',
                value: '${topTheme.name} · ${topTheme.score}',
                detail:
                    '${topTheme.stage.label} · 확산 ${topTheme.diffusion} · 위험 ${topTheme.risk}',
                color: scoreColor(topTheme.score),
              ),
            ],
            const Divider(height: 20),
            Text('관심 종목 신호', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 10),
            if (rankedEquities.isEmpty)
              Text(
                '관심 종목 데이터가 없습니다.',
                style: Theme.of(context).textTheme.bodyMedium,
              )
            else
              ...rankedEquities
                  .take(3)
                  .map(
                    (equity) => Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _ChecklistEquitySignalLine(
                        equity: equity,
                        quote: quotes[equity.symbol],
                      ),
                    ),
                  ),
          ],
        ],
      ),
    );
  }
}

class _ChecklistDataLine extends StatelessWidget {
  const _ChecklistDataLine({
    required this.icon,
    required this.title,
    required this.value,
    required this.detail,
    required this.color,
  });

  final IconData icon;
  final String title;
  final String value;
  final String detail;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, color: color, size: 20),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: Theme.of(context).textTheme.bodyMedium),
              const SizedBox(height: 3),
              Text(
                value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 3),
              Text(
                detail,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _ChecklistEquitySignalLine extends StatelessWidget {
  const _ChecklistEquitySignalLine({required this.equity, this.quote});

  final EquityFlow equity;
  final LiveQuote? quote;

  @override
  Widget build(BuildContext context) {
    final color = scoreColor(equity.flowScore);
    final effectiveChange = quote?.changePercent ?? equity.changePercent;
    final effectivePrice =
        quote?.priceLabel(equity.region) ?? equity.priceLabel;
    final changeColor = effectiveChange >= 0 ? AppColors.green : AppColors.red;

    return Row(
      children: [
        _SymbolAvatar(symbol: equity.symbol, color: color, size: 34),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '${equity.name} · ${equity.symbol}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 3),
              Text(
                '${equity.theme} · ${equity.stage.label}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
          ),
        ),
        const SizedBox(width: 8),
        Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(effectivePrice, style: Theme.of(context).textTheme.labelLarge),
            const SizedBox(height: 3),
            Text(
              effectiveChange >= 0
                  ? '+${effectiveChange.toStringAsFixed(2)}%'
                  : '${effectiveChange.toStringAsFixed(2)}%',
              style: TextStyle(
                color: changeColor,
                fontSize: 12,
                fontWeight: FontWeight.w800,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _ChecklistItemRow extends StatelessWidget {
  const _ChecklistItemRow({
    required this.item,
    required this.enabled,
    required this.onChanged,
    this.onRemove,
  });

  final InvestmentChecklistItem item;
  final bool enabled;
  final ValueChanged<bool> onChanged;
  final VoidCallback? onRemove;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Checkbox(
            value: item.checked,
            onChanged: enabled
                ? (value) {
                    if (value != null) {
                      onChanged(value);
                    }
                  }
                : null,
          ),
          Expanded(
            child: Text(
              item.label,
              style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                decoration: item.checked ? TextDecoration.lineThrough : null,
                color: item.checked ? AppColors.muted : AppColors.ink,
              ),
            ),
          ),
          if (onRemove != null)
            IconButton(
              tooltip: '항목 삭제',
              onPressed: enabled ? onRemove : null,
              icon: const Icon(Icons.close, size: 18),
            ),
        ],
      ),
    );
  }
}

class _ChecklistCalendarDayCell extends StatelessWidget {
  const _ChecklistCalendarDayCell({
    required this.date,
    required this.day,
    required this.selectedDate,
    required this.onSelected,
  });

  final DateTime? date;
  final InvestmentChecklistDay? day;
  final DateTime selectedDate;
  final VoidCallback? onSelected;

  @override
  Widget build(BuildContext context) {
    final date = this.date;
    if (date == null) {
      return const SizedBox.shrink();
    }

    final isSelected = DateUtils.isSameDay(date, selectedDate);
    final isToday = DateUtils.isSameDay(date, DateTime.now());
    final hasActivity = day?.hasActivity ?? false;
    final isComplete = day?.isComplete ?? false;
    final color = isSelected
        ? AppColors.green
        : isComplete
        ? AppColors.green
        : hasActivity
        ? AppColors.amber
        : AppColors.muted;

    return GestureDetector(
      onTap: onSelected,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: isSelected
              ? AppColors.green.withValues(alpha: 0.14)
              : color.withValues(alpha: hasActivity || isComplete ? 0.1 : 0.04),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: isSelected
                ? AppColors.green
                : isToday
                ? AppColors.blue
                : AppColors.line,
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 6),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                '${date.day}',
                style: Theme.of(context).textTheme.labelLarge?.copyWith(
                  color: isSelected ? AppColors.green : AppColors.ink,
                ),
              ),
              const SizedBox(height: 5),
              if (isComplete)
                Icon(Icons.check_circle, color: AppColors.green, size: 15)
              else if (hasActivity)
                Text(
                  '${day!.completedCount}/${day!.totalCount}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: AppColors.amber,
                    fontSize: 10,
                    fontWeight: FontWeight.w800,
                    height: 1,
                  ),
                )
              else
                SizedBox(
                  width: 5,
                  height: 5,
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      color: isToday ? AppColors.blue : AppColors.line,
                      shape: BoxShape.circle,
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class JournalScreen extends StatelessWidget {
  const JournalScreen({
    required this.entries,
    required this.onAddEntry,
    super.key,
  });

  final List<JournalEntry> entries;
  final VoidCallback onAddEntry;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
      children: [
        SectionHeader(
          title: '감 기록',
          trailing: IconButton.filled(
            tooltip: '새 기록',
            onPressed: onAddEntry,
            icon: const Icon(Icons.add),
          ),
        ),
        const SizedBox(height: 10),
        if (entries.isEmpty)
          const EmptyState(message: '이 프로필의 기록이 없습니다.')
        else
          ...entries.map(
            (entry) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: AppCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        FlowChip(
                          label: entry.symbol,
                          color: AppColors.charcoal,
                        ),
                        const SizedBox(width: 8),
                        FlowChip(label: entry.emotion, color: AppColors.blue),
                        const Spacer(),
                        Text(
                          entry.createdLabel,
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    Text(
                      entry.thesis,
                      style: Theme.of(context).textTheme.bodyLarge,
                    ),
                    const SizedBox(height: 14),
                    FactorBar(
                      label: '확신도',
                      value: entry.confidence,
                      color: scoreColor(entry.confidence),
                    ),
                    const SizedBox(height: 12),
                    Text(
                      entry.outcome,
                      style: Theme.of(context).textTheme.labelLarge,
                    ),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class JournalComposer extends StatefulWidget {
  const JournalComposer({
    required this.user,
    required this.equities,
    super.key,
  });

  final AppUser user;
  final List<EquityFlow> equities;

  @override
  State<JournalComposer> createState() => _JournalComposerState();
}

class _JournalComposerState extends State<JournalComposer> {
  late String _symbol;
  String _emotion = '확신';
  double _confidence = 70;
  final _thesisController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _symbol = widget.equities.first.symbol;
  }

  @override
  void dispose() {
    _thesisController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          4,
          20,
          20 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('새 감 기록', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              initialValue: _symbol,
              decoration: const InputDecoration(
                labelText: '종목',
                border: OutlineInputBorder(),
              ),
              items: [
                for (final equity in widget.equities)
                  DropdownMenuItem<String>(
                    value: equity.symbol,
                    child: Text('${equity.name} · ${equity.symbol}'),
                  ),
              ],
              onChanged: (value) {
                if (value != null) {
                  setState(() => _symbol = value);
                }
              },
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: _emotion,
              decoration: const InputDecoration(
                labelText: '감정',
                border: OutlineInputBorder(),
              ),
              items: const [
                DropdownMenuItem(value: '확신', child: Text('확신')),
                DropdownMenuItem(value: '대기', child: Text('대기')),
                DropdownMenuItem(value: '경계', child: Text('경계')),
                DropdownMenuItem(value: '공격', child: Text('공격')),
              ],
              onChanged: (value) {
                if (value != null) {
                  setState(() => _emotion = value);
                }
              },
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _thesisController,
              maxLines: 3,
              decoration: const InputDecoration(
                labelText: '판단 근거',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: Text(
                    '확신도 ${_confidence.round()}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                SizedBox(
                  width: 190,
                  child: Slider(
                    value: _confidence,
                    min: 0,
                    max: 100,
                    divisions: 20,
                    label: '${_confidence.round()}',
                    onChanged: (value) => setState(() => _confidence = value),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: () {
                  final thesis = _thesisController.text.trim();
                  if (thesis.isEmpty) {
                    return;
                  }

                  Navigator.of(context).pop(
                    JournalEntry(
                      id: 'j-${DateTime.now().millisecondsSinceEpoch}',
                      userId: widget.user.id,
                      symbol: _symbol,
                      thesis: thesis,
                      emotion: _emotion,
                      confidence: _confidence.round(),
                      outcome: '검증 전 / 관찰',
                      createdLabel: '방금',
                    ),
                  );
                },
                icon: const Icon(Icons.check),
                label: const Text('저장'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    required this.apiSources,
    required this.dataApiKeySettings,
    required this.dataApiKeysLoaded,
    required this.onSaveDataApiKeySettings,
    required this.tossSettings,
    required this.settingsLoaded,
    required this.onSaveTossSettings,
    required this.onTestTossConnection,
    required this.themePreference,
    required this.themeSettingsLoaded,
    required this.onThemePreferenceChanged,
    super.key,
  });

  final List<DataApiSource> apiSources;
  final DataApiKeySettings dataApiKeySettings;
  final bool dataApiKeysLoaded;
  final Future<void> Function(DataApiKeySettings settings)
  onSaveDataApiKeySettings;
  final TossAccountSettings tossSettings;
  final bool settingsLoaded;
  final Future<void> Function(TossAccountSettings settings) onSaveTossSettings;
  final Future<TossDirectApiProbeResult> Function(TossAccountSettings settings)
  onTestTossConnection;
  final AppThemePreference themePreference;
  final bool themeSettingsLoaded;
  final Future<void> Function(AppThemePreference preference)
  onThemePreferenceChanged;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final Map<String, TextEditingController> _dataApiKeyControllers = {};
  final _accountAliasController = TextEditingController();
  final _accountHintController = TextEditingController();
  final _apiBaseUrlController = TextEditingController();
  final _testPathController = TextEditingController();
  final _appKeyController = TextEditingController();
  final _appSecretController = TextEditingController();
  final _accessTokenController = TextEditingController();
  final _accountNumberController = TextEditingController();
  bool _enabled = false;
  bool _readOnly = true;
  bool _hideDataApiKeys = true;
  bool _hideSecrets = true;
  bool _savingThemePreference = false;
  bool _savingDataApiKeys = false;
  bool _testingConnection = false;
  TossDirectApiProbeResult? _probeResult;

  @override
  void initState() {
    super.initState();
    _syncDataApiKeyControllers();
    _applySettings(widget.tossSettings);
  }

  @override
  void didUpdateWidget(covariant SettingsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.tossSettings != widget.tossSettings) {
      _applySettings(widget.tossSettings);
    }
    if (oldWidget.dataApiKeySettings != widget.dataApiKeySettings ||
        oldWidget.apiSources != widget.apiSources) {
      _syncDataApiKeyControllers();
    }
  }

  @override
  void dispose() {
    for (final controller in _dataApiKeyControllers.values) {
      controller.dispose();
    }
    _accountAliasController.dispose();
    _accountHintController.dispose();
    _apiBaseUrlController.dispose();
    _testPathController.dispose();
    _appKeyController.dispose();
    _appSecretController.dispose();
    _accessTokenController.dispose();
    _accountNumberController.dispose();
    super.dispose();
  }

  void _syncDataApiKeyControllers() {
    final sourceIds = widget.apiSources.map((source) => source.id).toSet();
    final removedIds = _dataApiKeyControllers.keys
        .where((id) => !sourceIds.contains(id))
        .toList(growable: false);
    for (final id in removedIds) {
      _dataApiKeyControllers.remove(id)?.dispose();
    }

    for (final source in widget.apiSources) {
      final controller = _dataApiKeyControllers.putIfAbsent(
        source.id,
        () => TextEditingController(),
      );
      controller.text = widget.dataApiKeySettings.keyFor(source.id);
    }
  }

  void _applySettings(TossAccountSettings settings) {
    _enabled = settings.enabled;
    _readOnly = settings.readOnly;
    _accountAliasController.text = settings.accountAlias;
    _accountHintController.text = settings.accountHint;
    _apiBaseUrlController.text = settings.apiBaseUrl;
    _testPathController.text = settings.testPath;
    _appKeyController.text = settings.appKey;
    _appSecretController.text = settings.appSecret;
    _accessTokenController.text = settings.accessToken;
    _accountNumberController.text = settings.accountNumber;
  }

  TossAccountSettings _currentSettings() {
    return TossAccountSettings(
      enabled: _enabled,
      accountAlias: _accountAliasController.text.trim(),
      accountHint: _accountHintController.text.trim(),
      apiBaseUrl: _apiBaseUrlController.text.trim(),
      appKey: _appKeyController.text.trim(),
      appSecret: _appSecretController.text.trim(),
      accessToken: _accessTokenController.text.trim(),
      accountNumber: _accountNumberController.text.trim(),
      testPath: _testPathController.text.trim(),
      readOnly: _readOnly,
      orderLocked: true,
    );
  }

  DataApiKeySettings _currentDataApiKeySettings() {
    var settings = DataApiKeySettings.empty();
    for (final source in widget.apiSources) {
      final value = _dataApiKeyControllers[source.id]?.text ?? '';
      settings = settings.copyWithKey(source.id, value);
    }
    return settings;
  }

  Future<void> _saveDataApiKeys() async {
    setState(() => _savingDataApiKeys = true);
    await widget.onSaveDataApiKeySettings(_currentDataApiKeySettings());
    if (!mounted) {
      return;
    }
    setState(() => _savingDataApiKeys = false);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('데이터 API key 로컬 DB에 저장됨')));
  }

  Future<void> _changeThemePreference(AppThemePreference preference) async {
    if (preference == widget.themePreference) {
      return;
    }
    setState(() => _savingThemePreference = true);
    await widget.onThemePreferenceChanged(preference);
    if (!mounted) {
      return;
    }
    setState(() => _savingThemePreference = false);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('화면 테마 저장됨')));
  }

  Future<void> _save() async {
    await widget.onSaveTossSettings(_currentSettings());
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('토스증권 설정 저장됨')));
  }

  Future<void> _testConnection() async {
    setState(() {
      _testingConnection = true;
      _probeResult = null;
    });

    final result = await widget.onTestTossConnection(_currentSettings());
    if (!mounted) {
      return;
    }

    setState(() {
      _testingConnection = false;
      _probeResult = result;
    });
  }

  @override
  Widget build(BuildContext context) {
    final sortedApis = widget.apiSources.toList(growable: false)
      ..sort((a, b) => a.priority.compareTo(b.priority));
    final currentDataApiSettings = _currentDataApiKeySettings();
    final configuredApiCount = currentDataApiSettings.configuredCount(
      sortedApis,
    );
    final effectiveSettings = _currentSettings();

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
      children: [
        const SectionHeader(title: '설정'),
        const SizedBox(height: 10),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: AppColors.blue.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(10),
                      child: Icon(
                        Icons.contrast_outlined,
                        color: AppColors.blue,
                        size: 22,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '화면 테마',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '기기 로컬 설정에 저장',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ],
                    ),
                  ),
                  FlowChip(
                    label: widget.themePreference.summary,
                    color: widget.themePreference == AppThemePreference.dark
                        ? AppColors.blue
                        : AppColors.charcoal,
                  ),
                ],
              ),
              const SizedBox(height: 14),
              SizedBox(
                width: double.infinity,
                child: SegmentedButton<AppThemePreference>(
                  showSelectedIcon: false,
                  selected: {widget.themePreference},
                  onSelectionChanged:
                      widget.themeSettingsLoaded && !_savingThemePreference
                      ? (selection) => _changeThemePreference(selection.first)
                      : null,
                  segments: const [
                    ButtonSegment(
                      value: AppThemePreference.system,
                      icon: Icon(Icons.settings_suggest_outlined, size: 18),
                      label: Text('시스템'),
                    ),
                    ButtonSegment(
                      value: AppThemePreference.light,
                      icon: Icon(Icons.light_mode_outlined, size: 18),
                      label: Text('라이트'),
                    ),
                    ButtonSegment(
                      value: AppThemePreference.dark,
                      icon: Icon(Icons.dark_mode_outlined, size: 18),
                      label: Text('다크'),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: AppColors.blue.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(10),
                      child: Icon(
                        Icons.key_outlined,
                        color: AppColors.blue,
                        size: 22,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '데이터 API key',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '등록한 key를 기기 로컬 DB에 저장',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: _hideDataApiKeys ? 'API key 보기' : 'API key 숨기기',
                    onPressed: () =>
                        setState(() => _hideDataApiKeys = !_hideDataApiKeys),
                    icon: Icon(
                      _hideDataApiKeys
                          ? Icons.visibility_outlined
                          : Icons.visibility_off_outlined,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  FlowChip(
                    label: '$configuredApiCount/${sortedApis.length} key',
                    color: configuredApiCount == sortedApis.length
                        ? AppColors.green
                        : AppColors.amber,
                  ),
                  FlowChip(label: '읽기 전용 데이터', color: AppColors.blue),
                  FlowChip(label: '로컬 DB 저장', color: AppColors.charcoal),
                ],
              ),
              const SizedBox(height: 16),
              for (var index = 0; index < sortedApis.length; index++) ...[
                DataApiKeyField(
                  api: sortedApis[index],
                  controller: _dataApiKeyControllers[sortedApis[index].id]!,
                  enabled: widget.dataApiKeysLoaded,
                  obscureText: _hideDataApiKeys,
                  hasKey: currentDataApiSettings.hasKeyFor(
                    sortedApis[index].id,
                  ),
                  onChanged: () => setState(() {}),
                ),
                if (index != sortedApis.length - 1) ...[
                  const SizedBox(height: 14),
                  const Divider(height: 1),
                  const SizedBox(height: 14),
                ],
              ],
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: widget.dataApiKeysLoaded && !_savingDataApiKeys
                      ? _saveDataApiKeys
                      : null,
                  icon: _savingDataApiKeys
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save_outlined),
                  label: const Text('데이터 API key 저장'),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: AppColors.green.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(10),
                      child: Icon(
                        Icons.account_balance_outlined,
                        color: AppColors.green,
                        size: 22,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '토스증권 계정',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '앱에서 Open API 직접 호출 · 로컬 DB 저장',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ],
                    ),
                  ),
                  Switch(
                    value: _enabled,
                    onChanged: widget.settingsLoaded
                        ? (value) => setState(() => _enabled = value)
                        : null,
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  FlowChip(
                    label: _enabled ? '활성' : '비활성',
                    color: _enabled ? AppColors.green : AppColors.muted,
                  ),
                  FlowChip(
                    label: effectiveSettings.hasApiBaseUrl
                        ? 'URL 설정'
                        : 'URL 필요',
                    color: effectiveSettings.hasApiBaseUrl
                        ? AppColors.green
                        : AppColors.amber,
                  ),
                  FlowChip(
                    label: effectiveSettings.hasCredential ? '인증 설정' : '인증 필요',
                    color: effectiveSettings.hasCredential
                        ? AppColors.green
                        : AppColors.amber,
                  ),
                  FlowChip(
                    label: _readOnly ? 'read-only' : 'read/write',
                    color: _readOnly ? AppColors.blue : AppColors.red,
                  ),
                  FlowChip(label: '주문 잠금', color: AppColors.red),
                ],
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _accountAliasController,
                decoration: const InputDecoration(
                  labelText: '계정 별칭',
                  hintText: '예: 토스 주계좌',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _accountHintController,
                decoration: const InputDecoration(
                  labelText: '계좌 식별값',
                  hintText: '예: 끝 4자리 또는 내부 별칭',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _accountNumberController,
                keyboardType: TextInputType.text,
                decoration: const InputDecoration(
                  labelText: '계좌번호',
                  hintText: 'Open API에서 쓰는 계좌 식별값',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _apiBaseUrlController,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: 'Open API 기본 URL',
                  hintText: 'https://...',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _testPathController,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: '연결 테스트 경로',
                  hintText: '/v1/...',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _appKeyController,
                obscureText: _hideSecrets,
                keyboardType: TextInputType.visiblePassword,
                decoration: InputDecoration(
                  labelText: '앱 키',
                  border: const OutlineInputBorder(),
                  suffixIcon: IconButton(
                    tooltip: _hideSecrets ? '보기' : '숨기기',
                    icon: Icon(
                      _hideSecrets
                          ? Icons.visibility_outlined
                          : Icons.visibility_off_outlined,
                    ),
                    onPressed: () =>
                        setState(() => _hideSecrets = !_hideSecrets),
                  ),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _appSecretController,
                obscureText: _hideSecrets,
                keyboardType: TextInputType.visiblePassword,
                decoration: const InputDecoration(
                  labelText: '앱 시크릿',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _accessTokenController,
                obscureText: _hideSecrets,
                keyboardType: TextInputType.visiblePassword,
                decoration: const InputDecoration(
                  labelText: '액세스 토큰',
                  hintText: 'Bearer 토큰 또는 토큰 값',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              SwitchListTile(
                value: _readOnly,
                onChanged: (value) => setState(() => _readOnly = value),
                contentPadding: EdgeInsets.zero,
                title: const Text('읽기 전용 연결'),
                subtitle: const Text('시세, 잔고, 보유 종목 조회만 허용'),
              ),
              SwitchListTile(
                value: true,
                onChanged: null,
                contentPadding: EdgeInsets.zero,
                title: const Text('주문 기능 잠금'),
                subtitle: const Text('자동매매와 주문은 별도 서버 검증 전까지 비활성화'),
              ),
              const SizedBox(height: 8),
              Text(
                '직접 호출 모드는 API key, secret, token을 이 기기의 로컬 DB에 저장합니다. 중앙 서버로 전송하지 않지만, 웹 배포에서는 브라우저 저장소와 네트워크 요청에 노출될 수 있습니다.',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
              const SizedBox(height: 16),
              if (_probeResult != null) ...[
                DecoratedBox(
                  decoration: BoxDecoration(
                    color: (_probeResult!.ok ? AppColors.green : AppColors.red)
                        .withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          _probeResult!.ok
                              ? Icons.check_circle_outline
                              : Icons.error_outline,
                          color: _probeResult!.ok
                              ? AppColors.green
                              : AppColors.red,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            _probeResult!.statusLabel,
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 12),
              ],
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: widget.settingsLoaded && !_testingConnection
                      ? _testConnection
                      : null,
                  icon: _testingConnection
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.cloud_sync_outlined),
                  label: const Text('연결 테스트'),
                ),
              ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: widget.settingsLoaded ? _save : null,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('저장'),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _ScoreDial extends StatelessWidget {
  const _ScoreDial({required this.score, required this.color});

  final int score;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 58,
      height: 58,
      child: Stack(
        alignment: Alignment.center,
        children: [
          CircularProgressIndicator(
            value: score.clamp(0, 100) / 100,
            strokeWidth: 6,
            backgroundColor: AppColors.line,
            valueColor: AlwaysStoppedAnimation<Color>(color),
          ),
          Text(
            '$score',
            style: Theme.of(
              context,
            ).textTheme.labelLarge?.copyWith(color: color),
          ),
        ],
      ),
    );
  }
}

class _MiniStat extends StatelessWidget {
  const _MiniStat({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final int value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.bodyMedium),
        const SizedBox(height: 3),
        Text(
          '$value',
          style: Theme.of(
            context,
          ).textTheme.titleMedium?.copyWith(color: color),
        ),
      ],
    );
  }
}

class _SymbolAvatar extends StatelessWidget {
  const _SymbolAvatar({
    required this.symbol,
    required this.color,
    this.size = 40,
  });

  final String symbol;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    final initials = symbol.length <= 2 ? symbol : symbol.substring(0, 2);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.26)),
      ),
      child: SizedBox(
        width: size,
        height: size,
        child: Center(
          child: Text(
            initials,
            style: TextStyle(
              color: color,
              fontSize: size >= 48 ? 16 : 13,
              fontWeight: FontWeight.w900,
            ),
          ),
        ),
      ),
    );
  }
}

class EmptyState extends StatelessWidget {
  const EmptyState({required this.message, super.key});

  final String message;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Row(
        children: [
          Icon(Icons.info_outline, color: AppColors.muted),
          const SizedBox(width: 10),
          Expanded(
            child: Text(message, style: Theme.of(context).textTheme.bodyMedium),
          ),
        ],
      ),
    );
  }
}

int _compareEconomicFeeds(EconomicFeedItem a, EconomicFeedItem b) {
  final impactCompare = b.impactScore.compareTo(a.impactScore);
  if (impactCompare != 0) {
    return impactCompare;
  }
  final aDate = a.publishedAt;
  final bDate = b.publishedAt;
  if (aDate != null && bDate != null) {
    return bDate.compareTo(aDate);
  }
  if (aDate != null) {
    return -1;
  }
  if (bDate != null) {
    return 1;
  }
  return a.title.compareTo(b.title);
}

Color _feedStatusColor(EconomicFeedFetchStatus status) {
  switch (status) {
    case EconomicFeedFetchStatus.idle:
      return AppColors.muted;
    case EconomicFeedFetchStatus.loading:
      return AppColors.blue;
    case EconomicFeedFetchStatus.ready:
      return AppColors.green;
    case EconomicFeedFetchStatus.partial:
      return AppColors.amber;
    case EconomicFeedFetchStatus.failed:
      return AppColors.red;
  }
}

Color _cryptoStatusColor(CryptoFetchStatus status) {
  switch (status) {
    case CryptoFetchStatus.idle:
      return AppColors.muted;
    case CryptoFetchStatus.loading:
      return AppColors.blue;
    case CryptoFetchStatus.ready:
      return AppColors.green;
    case CryptoFetchStatus.partial:
      return AppColors.amber;
    case CryptoFetchStatus.failed:
      return AppColors.red;
  }
}

Color _cryptoChangeColor(double value) {
  if (value > 0) {
    return AppColors.green;
  }
  if (value < 0) {
    return AppColors.red;
  }
  return AppColors.muted;
}

String _formatUsdPrice(double value) {
  if (value >= 1000) {
    return '\$${value.toStringAsFixed(0)}';
  }
  if (value >= 1) {
    return '\$${value.toStringAsFixed(2)}';
  }
  if (value >= 0.01) {
    return '\$${value.toStringAsFixed(4)}';
  }
  return '\$${value.toStringAsFixed(6)}';
}

String _formatUsdCompact(double value) {
  final absolute = value.abs();
  if (absolute >= 1000000000000) {
    return '\$${(value / 1000000000000).toStringAsFixed(2)}T';
  }
  if (absolute >= 1000000000) {
    return '\$${(value / 1000000000).toStringAsFixed(1)}B';
  }
  if (absolute >= 1000000) {
    return '\$${(value / 1000000).toStringAsFixed(1)}M';
  }
  if (absolute >= 1000) {
    return '\$${(value / 1000).toStringAsFixed(1)}K';
  }
  return _formatUsdPrice(value);
}

String _formatPercentSigned(double value) {
  final prefix = value > 0 ? '+' : '';
  return '$prefix${value.toStringAsFixed(2)}%';
}

String _formatCryptoUpdated(DateTime? value) {
  if (value == null) {
    return '업데이트 대기';
  }
  return '${_formatClock(value)} 업데이트';
}

String _feedUrlHost(String url) {
  final uri = Uri.tryParse(url);
  final host = uri?.host ?? '';
  if (host.isEmpty) {
    return '원문 링크';
  }
  return host.replaceFirst(RegExp(r'^www\.'), '');
}

IconData _assetClassIcon(CapitalFlowAssetClass assetClass) {
  switch (assetClass) {
    case CapitalFlowAssetClass.equityIndex:
      return Icons.stacked_line_chart;
    case CapitalFlowAssetClass.sector:
      return Icons.hub_outlined;
    case CapitalFlowAssetClass.crypto:
      return Icons.currency_bitcoin;
    case CapitalFlowAssetClass.commodity:
      return Icons.diamond_outlined;
    case CapitalFlowAssetClass.bond:
      return Icons.account_balance_outlined;
    case CapitalFlowAssetClass.currency:
      return Icons.currency_exchange;
    case CapitalFlowAssetClass.alternative:
      return Icons.category_outlined;
  }
}

IconData _feedTypeIcon(EconomicFeedType type) {
  switch (type) {
    case EconomicFeedType.macro:
      return Icons.public_outlined;
    case EconomicFeedType.liquidity:
      return Icons.waterfall_chart;
    case EconomicFeedType.policy:
      return Icons.account_balance_outlined;
    case EconomicFeedType.flow:
      return Icons.route_outlined;
    case EconomicFeedType.earnings:
      return Icons.request_quote_outlined;
    case EconomicFeedType.risk:
      return Icons.warning_amber_outlined;
  }
}

Color _feedTypeColor(EconomicFeedType type) {
  switch (type) {
    case EconomicFeedType.macro:
      return AppColors.blue;
    case EconomicFeedType.liquidity:
      return AppColors.green;
    case EconomicFeedType.policy:
      return AppColors.charcoal;
    case EconomicFeedType.flow:
      return AppColors.amber;
    case EconomicFeedType.earnings:
      return AppColors.muted;
    case EconomicFeedType.risk:
      return AppColors.red;
  }
}

Color _assetClassColor(CapitalFlowAssetClass assetClass) {
  switch (assetClass) {
    case CapitalFlowAssetClass.equityIndex:
      return AppColors.green;
    case CapitalFlowAssetClass.sector:
      return AppColors.blue;
    case CapitalFlowAssetClass.crypto:
      return AppColors.amber;
    case CapitalFlowAssetClass.commodity:
      return AppColors.charcoal;
    case CapitalFlowAssetClass.bond:
      return AppColors.muted;
    case CapitalFlowAssetClass.currency:
      return AppColors.red;
    case CapitalFlowAssetClass.alternative:
      return AppColors.blue;
  }
}

IconData _apiStatusIcon(ApiIntegrationStatus status) {
  switch (status) {
    case ApiIntegrationStatus.live:
      return Icons.check_circle_outline;
    case ApiIntegrationStatus.configurable:
      return Icons.tune_outlined;
    case ApiIntegrationStatus.needed:
      return Icons.add_link;
    case ApiIntegrationStatus.vendorNeeded:
      return Icons.manage_search_outlined;
  }
}

Color _apiStatusColor(ApiIntegrationStatus status) {
  switch (status) {
    case ApiIntegrationStatus.live:
      return AppColors.green;
    case ApiIntegrationStatus.configurable:
      return AppColors.blue;
    case ApiIntegrationStatus.needed:
      return AppColors.amber;
    case ApiIntegrationStatus.vendorNeeded:
      return AppColors.red;
  }
}

DateTime _shiftChecklistMonth(DateTime month, int offset) {
  return DateTime(month.year, month.month + offset);
}

List<DateTime?> _calendarDates(DateTime month) {
  final start = checklistMonthStart(month);
  final daysInMonth = DateTime(start.year, start.month + 1, 0).day;
  final cells = <DateTime?>[
    for (var i = 0; i < start.weekday - 1; i++) null,
    for (var day = 1; day <= daysInMonth; day++)
      DateTime(start.year, start.month, day),
  ];
  while (cells.length % 7 != 0) {
    cells.add(null);
  }
  return cells;
}

String _formatChecklistMonth(DateTime date) {
  return '${date.year}.${date.month.toString().padLeft(2, '0')}';
}

String _formatChecklistDate(DateTime date) {
  const weekdays = ['월', '화', '수', '목', '금', '토', '일'];
  final month = date.month.toString().padLeft(2, '0');
  final day = date.day.toString().padLeft(2, '0');
  return '${date.year}.$month.$day (${weekdays[date.weekday - 1]})';
}

String _formatClock(DateTime value) {
  final hour = value.hour.toString().padLeft(2, '0');
  final minute = value.minute.toString().padLeft(2, '0');
  return '$hour:$minute';
}
