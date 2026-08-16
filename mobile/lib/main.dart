import 'package:flutter/material.dart';

import 'src/data/crypto_market_service.dart';
import 'src/data/economic_feed_service.dart';
import 'src/data/flow_repository.dart';
import 'src/data/settings_repository.dart';
import 'src/screens/app_shell.dart';
import 'src/theme/app_theme.dart';

void main() {
  runApp(const OrbitAlphaApp());
}

class OrbitAlphaApp extends StatefulWidget {
  const OrbitAlphaApp({
    this.repository = const MockFlowRepository(),
    this.cryptoMarketService,
    this.economicFeedService,
    super.key,
  });

  final FlowRepository repository;
  final CryptoMarketService? cryptoMarketService;
  final EconomicFeedService? economicFeedService;

  @override
  State<OrbitAlphaApp> createState() => _OrbitAlphaAppState();
}

class _OrbitAlphaAppState extends State<OrbitAlphaApp>
    with WidgetsBindingObserver {
  final _settingsRepository = SettingsRepository();
  AppThemePreference _themePreference = AppThemePreference.dark;
  bool _themeSettingsLoaded = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadThemePreference();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangePlatformBrightness() {
    if (_themePreference == AppThemePreference.system) {
      setState(() {});
    }
  }

  Future<void> _loadThemePreference() async {
    final preference = await _settingsRepository.loadThemePreference();
    if (!mounted) {
      return;
    }
    setState(() {
      _themePreference = preference;
      _themeSettingsLoaded = true;
    });
  }

  Future<void> _changeThemePreference(AppThemePreference preference) async {
    setState(() => _themePreference = preference);
    await _settingsRepository.saveThemePreference(preference);
    if (!mounted) {
      return;
    }
    setState(() => _themeSettingsLoaded = true);
  }

  @override
  Widget build(BuildContext context) {
    final platformBrightness =
        WidgetsBinding.instance.platformDispatcher.platformBrightness;
    AppColors.use(AppTheme.paletteFor(_themePreference, platformBrightness));

    return MaterialApp(
      title: 'Orbit Alpha',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: _themePreference.themeMode,
      home: SelectionArea(
        child: AppShell(
          repository: widget.repository,
          cryptoMarketService: widget.cryptoMarketService,
          economicFeedService: widget.economicFeedService,
          themePreference: _themePreference,
          themeSettingsLoaded: _themeSettingsLoaded,
          onThemePreferenceChanged: _changeThemePreference,
        ),
      ),
    );
  }
}

@Deprecated('Use OrbitAlphaApp')
class MarketFlowApp extends OrbitAlphaApp {
  const MarketFlowApp({
    super.repository,
    super.cryptoMarketService,
    super.economicFeedService,
    super.key,
  });
}
