import 'package:shared_preferences/shared_preferences.dart';

import '../models/market_models.dart';

class SettingsRepository {
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
}
