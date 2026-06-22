import 'package:shared_preferences/shared_preferences.dart';

import 'local_settings_database.dart';
import '../models/market_models.dart';
import '../theme/app_theme.dart';

class SettingsRepository {
  SettingsRepository({LocalSettingsDatabase? database})
    : _database = database ?? const LocalSettingsDatabase();

  final LocalSettingsDatabase _database;

  static const _themePreferenceKey = 'app.themeMode';
  static const _legacyDataApiKeyPrefix = 'dataApi.key.';
  static const _legacyTossEnabledKey = 'toss.enabled';
  static const _legacyTossAccountAliasKey = 'toss.accountAlias';
  static const _legacyTossAccountHintKey = 'toss.accountHint';
  static const _legacyTossApiBaseUrlKey = 'toss.apiBaseUrl';
  static const _legacyTossBackendUrlKey = 'toss.backendUrl';
  static const _legacyTossAppKeyKey = 'toss.appKey';
  static const _legacyTossAppSecretKey = 'toss.appSecret';
  static const _legacyTossAccessTokenKey = 'toss.accessToken';
  static const _legacyTossAccountNumberKey = 'toss.accountNumber';
  static const _legacyTossTestPathKey = 'toss.testPath';
  static const _legacyTossReadOnlyKey = 'toss.readOnly';
  static const _legacyTossOrderLockedKey = 'toss.orderLocked';

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
    final keys = <String, String>{};
    for (final source in sources) {
      final value =
          await _readStringWithLegacy(
            LocalSettingsDatabase.dataApiKeyStorageKey(source.id),
            _legacyDataApiKey(source.id),
          ) ??
          '';
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
    for (final source in sources) {
      final value = settings.keyFor(source.id).trim();
      final key = LocalSettingsDatabase.dataApiKeyStorageKey(source.id);
      if (value.isEmpty) {
        await _database.remove(key);
        await _database.remove(
          LocalSettingsDatabase.dataApiKeyUpdatedAtStorageKey(source.id),
        );
      } else {
        await _database.writeString(key, value);
        await _database.markDataApiKeyUpdated(source.id);
      }
    }
  }

  Future<TossAccountSettings> loadTossAccountSettings() async {
    final enabled =
        await _readBoolWithLegacy(
          LocalSettingsDatabase.tossStorageKey('enabled'),
          _legacyTossEnabledKey,
        ) ??
        false;
    final accountAlias =
        await _readStringWithLegacy(
          LocalSettingsDatabase.tossStorageKey('accountAlias'),
          _legacyTossAccountAliasKey,
        ) ??
        '';
    final accountHint =
        await _readStringWithLegacy(
          LocalSettingsDatabase.tossStorageKey('accountHint'),
          _legacyTossAccountHintKey,
        ) ??
        '';
    var apiBaseUrl = await _readStringWithLegacy(
      LocalSettingsDatabase.tossStorageKey('apiBaseUrl'),
      _legacyTossApiBaseUrlKey,
    );
    apiBaseUrl ??= await _readStringWithLegacy(
      LocalSettingsDatabase.tossStorageKey('apiBaseUrl'),
      _legacyTossBackendUrlKey,
    );
    final appKey =
        await _readStringWithLegacy(
          LocalSettingsDatabase.tossStorageKey('appKey'),
          _legacyTossAppKeyKey,
        ) ??
        '';
    final appSecret =
        await _readStringWithLegacy(
          LocalSettingsDatabase.tossStorageKey('appSecret'),
          _legacyTossAppSecretKey,
        ) ??
        '';
    final accessToken =
        await _readStringWithLegacy(
          LocalSettingsDatabase.tossStorageKey('accessToken'),
          _legacyTossAccessTokenKey,
        ) ??
        '';
    final accountNumber =
        await _readStringWithLegacy(
          LocalSettingsDatabase.tossStorageKey('accountNumber'),
          _legacyTossAccountNumberKey,
        ) ??
        '';
    final testPath =
        await _readStringWithLegacy(
          LocalSettingsDatabase.tossStorageKey('testPath'),
          _legacyTossTestPathKey,
        ) ??
        '';
    final readOnly =
        await _readBoolWithLegacy(
          LocalSettingsDatabase.tossStorageKey('readOnly'),
          _legacyTossReadOnlyKey,
        ) ??
        true;
    final orderLocked =
        await _readBoolWithLegacy(
          LocalSettingsDatabase.tossStorageKey('orderLocked'),
          _legacyTossOrderLockedKey,
        ) ??
        true;

    return TossAccountSettings(
      enabled: enabled,
      accountAlias: accountAlias,
      accountHint: accountHint,
      apiBaseUrl: apiBaseUrl ?? '',
      appKey: appKey,
      appSecret: appSecret,
      accessToken: accessToken,
      accountNumber: accountNumber,
      testPath: testPath,
      readOnly: readOnly,
      orderLocked: orderLocked,
    );
  }

  Future<void> saveTossAccountSettings(TossAccountSettings settings) async {
    await _database.writeBool(
      LocalSettingsDatabase.tossStorageKey('enabled'),
      settings.enabled,
    );
    await _database.writeString(
      LocalSettingsDatabase.tossStorageKey('accountAlias'),
      settings.accountAlias,
    );
    await _database.writeString(
      LocalSettingsDatabase.tossStorageKey('accountHint'),
      settings.accountHint,
    );
    await _database.writeString(
      LocalSettingsDatabase.tossStorageKey('apiBaseUrl'),
      settings.apiBaseUrl,
    );
    await _database.writeString(
      LocalSettingsDatabase.tossStorageKey('appKey'),
      settings.appKey,
    );
    await _database.writeString(
      LocalSettingsDatabase.tossStorageKey('appSecret'),
      settings.appSecret,
    );
    await _database.writeString(
      LocalSettingsDatabase.tossStorageKey('accessToken'),
      settings.accessToken,
    );
    await _database.writeString(
      LocalSettingsDatabase.tossStorageKey('accountNumber'),
      settings.accountNumber,
    );
    await _database.writeString(
      LocalSettingsDatabase.tossStorageKey('testPath'),
      settings.testPath,
    );
    await _database.writeBool(
      LocalSettingsDatabase.tossStorageKey('readOnly'),
      settings.readOnly,
    );
    await _database.writeBool(
      LocalSettingsDatabase.tossStorageKey('orderLocked'),
      settings.orderLocked,
    );
  }

  Future<String?> _readStringWithLegacy(String key, String legacyKey) async {
    final stored = await _database.readString(key);
    if (stored != null) {
      return stored;
    }
    final prefs = await SharedPreferences.getInstance();
    final legacyValue = prefs.getString(legacyKey);
    if (legacyValue != null) {
      await _database.writeString(key, legacyValue);
    }
    return legacyValue;
  }

  Future<bool?> _readBoolWithLegacy(String key, String legacyKey) async {
    final stored = await _database.readBool(key);
    if (stored != null) {
      return stored;
    }
    final prefs = await SharedPreferences.getInstance();
    final legacyValue = prefs.getBool(legacyKey);
    if (legacyValue != null) {
      await _database.writeBool(key, legacyValue);
    }
    return legacyValue;
  }

  String _legacyDataApiKey(String apiId) {
    return '$_legacyDataApiKeyPrefix$apiId';
  }
}
