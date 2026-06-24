import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class SecureSettingsStore {
  const SecureSettingsStore({
    FlutterSecureStorage storage = const FlutterSecureStorage(
      aOptions: AndroidOptions(storageNamespace: 'digital_twin_toss'),
      iOptions: IOSOptions(
        accountName: 'digital_twin_toss',
        accessibility: KeychainAccessibility.unlocked_this_device,
        synchronizable: false,
      ),
      mOptions: MacOsOptions(
        accountName: 'digital_twin_toss',
        accessibility: KeychainAccessibility.unlocked_this_device,
        synchronizable: false,
      ),
    ),
  }) : _storage = storage;

  static const storageLabel = '기기 보안 저장소';

  final FlutterSecureStorage _storage;

  static String tossSecretKey(String field) {
    return 'secureSettings.toss.$field';
  }

  Future<String?> readString(String key) {
    return _storage.read(key: key);
  }

  Future<void> writeString(String key, String value) async {
    final normalized = value.trim();
    if (normalized.isEmpty) {
      await remove(key);
      return;
    }
    await _storage.write(key: key, value: normalized);
  }

  Future<void> remove(String key) {
    return _storage.delete(key: key);
  }
}
