import 'package:flutter/material.dart';

import '../data/flow_repository.dart';
import '../data/market_data_api.dart';
import '../data/settings_repository.dart';
import '../models/market_models.dart';
import '../theme/app_theme.dart';
import '../widgets/app_card.dart';
import '../widgets/region_switcher.dart';
import '../widgets/sparkline.dart';

class AppShell extends StatefulWidget {
  const AppShell({required this.repository, super.key});

  final FlowRepository repository;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _tabIndex = 0;
  MarketRegion _region = MarketRegion.all;
  late AppUser _currentUser;
  late List<JournalEntry> _journals;
  late AlphaVantageQuoteService _quoteService;
  late SettingsRepository _settingsRepository;
  late QuoteApiSnapshot _quoteSnapshot;
  Map<String, LiveQuote> _quotes = const {};
  TossAccountSettings _tossSettings = TossAccountSettings.defaults();
  bool _settingsLoaded = false;

  @override
  void initState() {
    super.initState();
    _currentUser = widget.repository.users.first;
    _journals = widget.repository.journals.toList();
    _quoteService = AlphaVantageQuoteService();
    _settingsRepository = SettingsRepository();
    _quoteSnapshot = _quoteService.initialSnapshot;
    _loadTossSettings();
    WidgetsBinding.instance.addPostFrameCallback((_) => _refreshLiveQuotes());
  }

  @override
  void dispose() {
    _quoteService.dispose();
    super.dispose();
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

  Future<void> _saveTossSettings(TossAccountSettings settings) async {
    await _settingsRepository.saveTossAccountSettings(settings);
    if (!mounted) {
      return;
    }
    setState(() => _tossSettings = settings);
  }

  bool _matchesRegion(MarketRegion region) {
    return _region == MarketRegion.all || _region == region;
  }

  List<MarketPulse> get _pulses {
    return widget.repository.marketPulses
        .where((pulse) => _matchesRegion(pulse.region))
        .toList(growable: false);
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
      ThemeBoardScreen(themes: _themes),
      WatchlistScreen(equities: _equities, quotes: _quotes),
      JournalScreen(entries: _userJournals, onAddEntry: _openJournalComposer),
      SettingsScreen(
        tossSettings: _tossSettings,
        settingsLoaded: _settingsLoaded,
        onSaveTossSettings: _saveTossSettings,
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
                    backgroundColor: AppColors.charcoal,
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
    required this.tossSettings,
    required this.settingsLoaded,
    required this.onSaveTossSettings,
    super.key,
  });

  final TossAccountSettings tossSettings;
  final bool settingsLoaded;
  final ValueChanged<TossAccountSettings> onSaveTossSettings;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _accountAliasController = TextEditingController();
  final _accountHintController = TextEditingController();
  final _backendUrlController = TextEditingController();
  bool _enabled = false;
  bool _readOnly = true;

  @override
  void initState() {
    super.initState();
    _applySettings(widget.tossSettings);
  }

  @override
  void didUpdateWidget(covariant SettingsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.tossSettings != widget.tossSettings) {
      _applySettings(widget.tossSettings);
    }
  }

  @override
  void dispose() {
    _accountAliasController.dispose();
    _accountHintController.dispose();
    _backendUrlController.dispose();
    super.dispose();
  }

  void _applySettings(TossAccountSettings settings) {
    _enabled = settings.enabled;
    _readOnly = settings.readOnly;
    _accountAliasController.text = settings.accountAlias;
    _accountHintController.text = settings.accountHint;
    _backendUrlController.text = settings.backendUrl;
  }

  Future<void> _save() async {
    final settings = TossAccountSettings(
      enabled: _enabled,
      accountAlias: _accountAliasController.text.trim(),
      accountHint: _accountHintController.text.trim(),
      backendUrl: _backendUrlController.text.trim(),
      readOnly: _readOnly,
      orderLocked: true,
    );
    widget.onSaveTossSettings(settings);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('토스증권 설정 저장됨')));
  }

  @override
  Widget build(BuildContext context) {
    final effectiveSettings = TossAccountSettings(
      enabled: _enabled,
      accountAlias: _accountAliasController.text.trim(),
      accountHint: _accountHintController.text.trim(),
      backendUrl: _backendUrlController.text.trim(),
      readOnly: _readOnly,
      orderLocked: true,
    );

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
                children: [
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: AppColors.green.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Padding(
                      padding: EdgeInsets.all(10),
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
                          'Toss Securities Open API',
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
                    label: effectiveSettings.hasBackend
                        ? 'backend 설정'
                        : 'backend 필요',
                    color: effectiveSettings.hasBackend
                        ? AppColors.green
                        : AppColors.amber,
                  ),
                  FlowChip(
                    label: _readOnly ? 'read-only' : 'read/write',
                    color: _readOnly ? AppColors.blue : AppColors.red,
                  ),
                  const FlowChip(label: '주문 잠금', color: AppColors.red),
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
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _accountHintController,
                decoration: const InputDecoration(
                  labelText: '계좌 식별값',
                  hintText: '예: 끝 4자리 또는 내부 별칭',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _backendUrlController,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: '백엔드 API URL',
                  hintText: 'https://api.example.com',
                  border: OutlineInputBorder(),
                ),
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
                '토스 API key와 secret은 앱에 저장하지 않고 서버 secret으로만 다룹니다.',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
              const SizedBox(height: 16),
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
          const Icon(Icons.info_outline, color: AppColors.muted),
          const SizedBox(width: 10),
          Expanded(
            child: Text(message, style: Theme.of(context).textTheme.bodyMedium),
          ),
        ],
      ),
    );
  }
}

String _formatClock(DateTime value) {
  final hour = value.hour.toString().padLeft(2, '0');
  final minute = value.minute.toString().padLeft(2, '0');
  return '$hour:$minute';
}
