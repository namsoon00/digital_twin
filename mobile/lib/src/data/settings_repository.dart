import 'package:shared_preferences/shared_preferences.dart';

import '../models/market_models.dart';

class SettingsRepository {
  static const _tossEnabledKey = 'toss.enabled';
  static const _tossAccountAliasKey = 'toss.accountAlias';
  static const _tossAccountHintKey = 'toss.accountHint';
  static const _tossBackendUrlKey = 'toss.backendUrl';
  static const _tossReadOnlyKey = 'toss.readOnly';
  static const _tossOrderLockedKey = 'toss.orderLocked';

  Future<TossAccountSettings> loadTossAccountSettings() async {
    final prefs = await SharedPreferences.getInstance();
    return TossAccountSettings(
      enabled: prefs.getBool(_tossEnabledKey) ?? false,
      accountAlias: prefs.getString(_tossAccountAliasKey) ?? '',
      accountHint: prefs.getString(_tossAccountHintKey) ?? '',
      backendUrl: prefs.getString(_tossBackendUrlKey) ?? '',
      readOnly: prefs.getBool(_tossReadOnlyKey) ?? true,
      orderLocked: prefs.getBool(_tossOrderLockedKey) ?? true,
    );
  }

  Future<void> saveTossAccountSettings(TossAccountSettings settings) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_tossEnabledKey, settings.enabled);
    await prefs.setString(_tossAccountAliasKey, settings.accountAlias);
    await prefs.setString(_tossAccountHintKey, settings.accountHint);
    await prefs.setString(_tossBackendUrlKey, settings.backendUrl);
    await prefs.setBool(_tossReadOnlyKey, settings.readOnly);
    await prefs.setBool(_tossOrderLockedKey, settings.orderLocked);
  }
}
