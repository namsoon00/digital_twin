import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../models/market_models.dart';

class ChecklistRepository {
  static const _dayPrefix = 'investmentChecklist.day.';
  static const _knownDaysKey = 'investmentChecklist.days';

  Future<InvestmentChecklistDay> loadDay(DateTime date) async {
    final prefs = await SharedPreferences.getInstance();
    final dateKey = checklistDateKey(date);
    final encoded = prefs.getString(_dayStorageKey(dateKey));
    if (encoded == null || encoded.isEmpty) {
      return InvestmentChecklistDay.defaults(dateKey);
    }

    try {
      final decoded = jsonDecode(encoded);
      if (decoded is Map<String, dynamic>) {
        return _mergeWithDefaults(InvestmentChecklistDay.fromJson(decoded));
      }
    } on FormatException {
      return InvestmentChecklistDay.defaults(dateKey);
    }

    return InvestmentChecklistDay.defaults(dateKey);
  }

  Future<Map<String, InvestmentChecklistDay>> loadMonth(DateTime month) async {
    final prefs = await SharedPreferences.getInstance();
    final monthPrefix = checklistDateKey(
      checklistMonthStart(month),
    ).substring(0, 7);
    final knownDays = prefs.getStringList(_knownDaysKey) ?? const [];
    final days = <String, InvestmentChecklistDay>{};

    for (final dateKey in knownDays.where(
      (key) => key.startsWith(monthPrefix),
    )) {
      final encoded = prefs.getString(_dayStorageKey(dateKey));
      if (encoded == null || encoded.isEmpty) {
        continue;
      }

      try {
        final decoded = jsonDecode(encoded);
        if (decoded is Map<String, dynamic>) {
          final day = _mergeWithDefaults(
            InvestmentChecklistDay.fromJson(decoded),
          );
          if (day.hasActivity) {
            days[dateKey] = day;
          }
        }
      } on FormatException {
        continue;
      }
    }

    return Map.unmodifiable(days);
  }

  Future<void> saveDay(InvestmentChecklistDay day) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _dayStorageKey(day.dateKey),
      jsonEncode(day.toJson()),
    );
    final knownDays = {
      ...prefs.getStringList(_knownDaysKey) ?? const <String>[],
      day.dateKey,
    }.toList(growable: false)..sort();
    await prefs.setStringList(_knownDaysKey, knownDays);
  }

  Future<void> resetDay(String dateKey) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_dayStorageKey(dateKey));
    final knownDays = (prefs.getStringList(_knownDaysKey) ?? const [])
        .where((key) => key != dateKey)
        .toList(growable: false);
    await prefs.setStringList(_knownDaysKey, knownDays);
  }

  InvestmentChecklistDay _mergeWithDefaults(InvestmentChecklistDay stored) {
    final defaults = InvestmentChecklistDay.defaults(stored.dateKey);
    final storedById = {for (final item in stored.items) item.id: item};
    final defaultIds = defaults.items.map((item) => item.id).toSet();
    final mergedDefaults = [
      for (final item in defaults.items)
        item.copyWith(checked: storedById[item.id]?.checked ?? false),
    ];
    final customItems = stored.items
        .where((item) => item.isCustom || !defaultIds.contains(item.id))
        .toList(growable: false);

    return stored.copyWith(items: [...mergedDefaults, ...customItems]);
  }

  String _dayStorageKey(String dateKey) {
    return '$_dayPrefix$dateKey';
  }
}
