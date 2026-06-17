import 'package:flutter/material.dart';

enum AppThemePreference {
  system('system'),
  light('light'),
  dark('dark');

  const AppThemePreference(this.storageValue);

  final String storageValue;

  ThemeMode get themeMode {
    return switch (this) {
      AppThemePreference.system => ThemeMode.system,
      AppThemePreference.light => ThemeMode.light,
      AppThemePreference.dark => ThemeMode.dark,
    };
  }

  String get label {
    return switch (this) {
      AppThemePreference.system => '시스템',
      AppThemePreference.light => '라이트',
      AppThemePreference.dark => '다크',
    };
  }

  String get summary {
    return switch (this) {
      AppThemePreference.system => '기기 설정을 따름',
      AppThemePreference.light => '밝은 금융 대시보드',
      AppThemePreference.dark => '어두운 금융 대시보드',
    };
  }

  static AppThemePreference fromStorage(String? value) {
    for (final preference in AppThemePreference.values) {
      if (preference.storageValue == value) {
        return preference;
      }
    }
    return AppThemePreference.dark;
  }
}

class AppPalette {
  const AppPalette({
    required this.canvas,
    required this.surface,
    required this.ink,
    required this.muted,
    required this.line,
    required this.green,
    required this.blue,
    required this.amber,
    required this.red,
    required this.charcoal,
  });

  final Color canvas;
  final Color surface;
  final Color ink;
  final Color muted;
  final Color line;
  final Color green;
  final Color blue;
  final Color amber;
  final Color red;
  final Color charcoal;
}

class AppColors {
  const AppColors._();

  static const darkPalette = AppPalette(
    canvas: Color(0xFF0B1017),
    surface: Color(0xFF111827),
    ink: Color(0xFFE5E7EB),
    muted: Color(0xFF98A2B3),
    line: Color(0xFF263241),
    green: Color(0xFF34D399),
    blue: Color(0xFF60A5FA),
    amber: Color(0xFFFBBF24),
    red: Color(0xFFF87171),
    charcoal: Color(0xFFCBD5E1),
  );

  static const lightPalette = AppPalette(
    canvas: Color(0xFFF4F6F8),
    surface: Color(0xFFFFFFFF),
    ink: Color(0xFF101828),
    muted: Color(0xFF667085),
    line: Color(0xFFD0D5DD),
    green: Color(0xFF047857),
    blue: Color(0xFF1D4ED8),
    amber: Color(0xFFB7791F),
    red: Color(0xFFB42318),
    charcoal: Color(0xFF344054),
  );

  static AppPalette _active = darkPalette;

  static void use(AppPalette palette) {
    _active = palette;
  }

  static Color get canvas => _active.canvas;
  static Color get surface => _active.surface;
  static Color get ink => _active.ink;
  static Color get muted => _active.muted;
  static Color get line => _active.line;
  static Color get green => _active.green;
  static Color get blue => _active.blue;
  static Color get amber => _active.amber;
  static Color get red => _active.red;
  static Color get charcoal => _active.charcoal;
}

class AppTheme {
  const AppTheme._();

  static ThemeData get dark => _build(AppColors.darkPalette, Brightness.dark);

  static ThemeData get light =>
      _build(AppColors.lightPalette, Brightness.light);

  static AppPalette paletteFor(
    AppThemePreference preference,
    Brightness platformBrightness,
  ) {
    return switch (preference) {
      AppThemePreference.system =>
        platformBrightness == Brightness.dark
            ? AppColors.darkPalette
            : AppColors.lightPalette,
      AppThemePreference.dark => AppColors.darkPalette,
      AppThemePreference.light => AppColors.lightPalette,
    };
  }

  static ThemeData _build(AppPalette palette, Brightness brightness) {
    final scheme =
        ColorScheme.fromSeed(
          seedColor: palette.blue,
          brightness: brightness,
        ).copyWith(
          surface: palette.surface,
          primary: palette.green,
          secondary: palette.blue,
          tertiary: palette.amber,
          error: palette.red,
          onSurface: palette.ink,
        );

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: palette.canvas,
      textTheme: TextTheme(
        displaySmall: TextStyle(
          fontSize: 28,
          height: 1.08,
          fontWeight: FontWeight.w900,
          color: palette.ink,
        ),
        headlineSmall: TextStyle(
          fontSize: 22,
          height: 1.18,
          fontWeight: FontWeight.w800,
          color: palette.ink,
        ),
        titleLarge: TextStyle(
          fontSize: 17,
          height: 1.2,
          fontWeight: FontWeight.w800,
          color: palette.ink,
        ),
        titleMedium: TextStyle(
          fontSize: 15,
          height: 1.22,
          fontWeight: FontWeight.w700,
          color: palette.ink,
        ),
        bodyLarge: TextStyle(
          fontSize: 15,
          height: 1.42,
          fontWeight: FontWeight.w500,
          color: palette.ink,
        ),
        bodyMedium: TextStyle(
          fontSize: 13,
          height: 1.36,
          fontWeight: FontWeight.w500,
          color: palette.muted,
        ),
        labelLarge: TextStyle(
          fontSize: 13,
          height: 1.15,
          fontWeight: FontWeight.w800,
          color: palette.ink,
        ),
      ),
      cardTheme: CardThemeData(
        color: palette.surface,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
          side: BorderSide(color: palette.line),
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: palette.canvas,
        foregroundColor: palette.ink,
        elevation: 0,
        centerTitle: false,
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: palette.surface,
        indicatorColor: palette.blue.withValues(alpha: 0.14),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return TextStyle(
            fontSize: 11,
            fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
            color: selected ? palette.ink : palette.muted,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            color: selected ? palette.ink : palette.muted,
            size: 22,
          );
        }),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith((states) {
            return states.contains(WidgetState.selected)
                ? palette.blue.withValues(alpha: 0.18)
                : palette.surface;
          }),
          foregroundColor: WidgetStateProperty.resolveWith((states) {
            return states.contains(WidgetState.selected)
                ? palette.ink
                : palette.muted;
          }),
          side: WidgetStateProperty.all(BorderSide(color: palette.line)),
          textStyle: WidgetStateProperty.all(
            const TextStyle(fontSize: 12, fontWeight: FontWeight.w800),
          ),
          visualDensity: VisualDensity.compact,
        ),
      ),
    );
  }
}
