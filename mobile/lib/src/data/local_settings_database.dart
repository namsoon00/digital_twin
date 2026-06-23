import 'package:shared_preferences/shared_preferences.dart';

class LocalSettingsDatabase {
  const LocalSettingsDatabase();

  static const storageLabel = '기기 로컬 DB';
  static const schemaVersion = 1;
  static const schemaVersionKey = 'localSettingsDb.schemaVersion';
  static const lastWriteAtKey = 'localSettingsDb.lastWriteAt';

  static String dataApiKeyStorageKey(String apiId) {
    return 'localSettingsDb.dataApiKey.$apiId';
  }

  static String dataApiKeyUpdatedAtStorageKey(String apiId) {
    return 'localSettingsDb.dataApiKeyUpdatedAt.$apiId';
  }

  static String dataApiVendorStorageKey(String apiId) {
    return 'localSettingsDb.dataApiVendor.$apiId';
  }

  static String tossStorageKey(String field) {
    return 'localSettingsDb.toss.$field';
  }

  static String apiCacheStorageKey(String apiId) {
    return 'localSettingsDb.apiCache.$apiId';
  }

  Future<String?> readString(String key) async {
    final prefs = await _open();
    return prefs.getString(key);
  }

  Future<bool?> readBool(String key) async {
    final prefs = await _open();
    return prefs.getBool(key);
  }

  Future<void> writeString(String key, String value) async {
    final prefs = await _open();
    await prefs.setString(key, value);
    await _touch(prefs);
  }

  Future<void> writeBool(String key, bool value) async {
    final prefs = await _open();
    await prefs.setBool(key, value);
    await _touch(prefs);
  }

  Future<void> remove(String key) async {
    final prefs = await _open();
    await prefs.remove(key);
    await _touch(prefs);
  }

  Future<void> markDataApiKeyUpdated(String apiId) async {
    await writeString(
      dataApiKeyUpdatedAtStorageKey(apiId),
      DateTime.now().toUtc().toIso8601String(),
    );
  }

  Future<SharedPreferences> _open() async {
    final prefs = await SharedPreferences.getInstance();
    final version = prefs.getInt(schemaVersionKey) ?? 0;
    if (version < schemaVersion) {
      await prefs.setInt(schemaVersionKey, schemaVersion);
    }
    return prefs;
  }

  Future<void> _touch(SharedPreferences prefs) async {
    await prefs.setString(
      lastWriteAtKey,
      DateTime.now().toUtc().toIso8601String(),
    );
  }
}
