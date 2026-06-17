import 'package:shared_preferences/shared_preferences.dart';

import '../models/market_models.dart';
import '../theme/app_theme.dart';

class SettingsRepository {
  static const _themePreferenceKey = 'app.themeMode';
  static const _dataApiKeyPrefix = 'dataApi.key.';
  static const _tossEnabledKey = 'toss.enabled';
  static const _tossAccountAliasKey = 'toss.accountAlias';
  static const _tossAccountHintKey = 'toss.accountHint';
  static const _tossApiBaseUrlKey = 'toss.apiBaseUrl';
  static const _legacyTossBackendUrlKey = 'toss.backendUrl';
  static const _tossAppKeyKey = 'toss.appKey';
  static const _tossAppSecretKey = 'toss.appSecret';
  static const _tossAccessTokenKey = 'toss.accessToken';
  static const _tossAccountNumberKey = 'toss.accountNumber';
  static const _tossTestPathKey = 'toss.testPath';
  static const _tossReadOnlyKey = 'toss.readOnly';
  static const _tossOrderLockedKey = 'toss.orderLocked';

  Future<AppThemePreference> loadThemePreference() async {
    final prefs = await SharedPreferences.getInstance();
    return AppThemePreference.fromStorage(prefs.getString(_themePreferenceKey));
  }

  Future<void> saveThemePreference(AppThemePreference preference) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_themePreferenceKey, preference.storageValue);
  }

  Future<DataApiKeySettings> loadDataApiKeySettings(
    Iterable<DataApiSource> sources,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    final keys = <String, String>{};
    for (final source in sources) {
      final value = prefs.getString(_dataApiKey(source.id)) ?? '';
      if (value.trim().isNotEmpty) {
        keys[source.id] = value;
      }
    }
    return DataApiKeySettings(keys: Map.unmodifiable(keys));
  }

  Future<void> saveDataApiKeySettings(
    DataApiKeySettings settings,
    Iterable<DataApiSource> sources,
  ) async {
    final prefs = await SharedPreferences.getInstance();
    for (final source in sources) {
      final value = settings.keyFor(source.id).trim();
      final key = _dataApiKey(source.id);
      if (value.isEmpty) {
        await prefs.remove(key);
      } else {
        await prefs.setString(key, value);
      }
    }
  }

  Future<TossAccountSettings> loadTossAccountSettings() async {
    final prefs = await SharedPreferences.getInstance();
    return TossAccountSettings(
      enabled: prefs.getBool(_tossEnabledKey) ?? false,
      accountAlias: prefs.getString(_tossAccountAliasKey) ?? '',
      accountHint: prefs.getString(_tossAccountHintKey) ?? '',
      apiBaseUrl:
          prefs.getString(_tossApiBaseUrlKey) ??
          prefs.getString(_legacyTossBackendUrlKey) ??
          '',
      appKey: prefs.getString(_tossAppKeyKey) ?? '',
      appSecret: prefs.getString(_tossAppSecretKey) ?? '',
      accessToken: prefs.getString(_tossAccessTokenKey) ?? '',
      accountNumber: prefs.getString(_tossAccountNumberKey) ?? '',
      testPath: prefs.getString(_tossTestPathKey) ?? '',
      readOnly: prefs.getBool(_tossReadOnlyKey) ?? true,
      orderLocked: prefs.getBool(_tossOrderLockedKey) ?? true,
    );
  }

  Future<void> saveTossAccountSettings(TossAccountSettings settings) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_tossEnabledKey, settings.enabled);
    await prefs.setString(_tossAccountAliasKey, settings.accountAlias);
    await prefs.setString(_tossAccountHintKey, settings.accountHint);
    await prefs.setString(_tossApiBaseUrlKey, settings.apiBaseUrl);
    await prefs.setString(_tossAppKeyKey, settings.appKey);
    await prefs.setString(_tossAppSecretKey, settings.appSecret);
    await prefs.setString(_tossAccessTokenKey, settings.accessToken);
    await prefs.setString(_tossAccountNumberKey, settings.accountNumber);
    await prefs.setString(_tossTestPathKey, settings.testPath);
    await prefs.setBool(_tossReadOnlyKey, settings.readOnly);
    await prefs.setBool(_tossOrderLockedKey, settings.orderLocked);
  }

  String _dataApiKey(String apiId) {
    return '$_dataApiKeyPrefix$apiId';
  }
}
