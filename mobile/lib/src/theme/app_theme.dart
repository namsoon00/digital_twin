import 'package:flutter/material.dart';

class AppColors {
  const AppColors._();

  static const canvas = Color(0xFFF5F7F2);
  static const surface = Color(0xFFFFFFFF);
  static const ink = Color(0xFF17201C);
  static const muted = Color(0xFF6A766F);
  static const line = Color(0xFFDDE5DC);
  static const green = Color(0xFF107C5B);
  static const blue = Color(0xFF2357A6);
  static const amber = Color(0xFFC88412);
  static const red = Color(0xFFC2413B);
  static const charcoal = Color(0xFF24312B);
}

class AppTheme {
  const AppTheme._();

  static ThemeData get light {
    final scheme =
        ColorScheme.fromSeed(
          seedColor: AppColors.green,
          brightness: Brightness.light,
        ).copyWith(
          surface: AppColors.surface,
          primary: AppColors.green,
          secondary: AppColors.blue,
          tertiary: AppColors.amber,
          error: AppColors.red,
        );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: AppColors.canvas,
      textTheme: const TextTheme(
        displaySmall: TextStyle(
          fontSize: 30,
          height: 1.08,
          fontWeight: FontWeight.w800,
          color: AppColors.ink,
        ),
        headlineSmall: TextStyle(
          fontSize: 22,
          height: 1.18,
          fontWeight: FontWeight.w800,
          color: AppColors.ink,
        ),
        titleLarge: TextStyle(
          fontSize: 18,
          height: 1.2,
          fontWeight: FontWeight.w800,
          color: AppColors.ink,
        ),
        titleMedium: TextStyle(
          fontSize: 15,
          height: 1.22,
          fontWeight: FontWeight.w700,
          color: AppColors.ink,
        ),
        bodyLarge: TextStyle(
          fontSize: 15,
          height: 1.42,
          fontWeight: FontWeight.w500,
          color: AppColors.ink,
        ),
        bodyMedium: TextStyle(
          fontSize: 13,
          height: 1.36,
          fontWeight: FontWeight.w500,
          color: AppColors.muted,
        ),
        labelLarge: TextStyle(
          fontSize: 13,
          height: 1.15,
          fontWeight: FontWeight.w800,
          color: AppColors.ink,
        ),
      ),
      cardTheme: CardThemeData(
        color: AppColors.surface,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
          side: const BorderSide(color: AppColors.line),
        ),
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.canvas,
        foregroundColor: AppColors.ink,
        elevation: 0,
        centerTitle: false,
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: AppColors.surface,
        indicatorColor: AppColors.green.withValues(alpha: 0.12),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return TextStyle(
            fontSize: 12,
            fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
            color: selected ? AppColors.green : AppColors.muted,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            color: selected ? AppColors.green : AppColors.muted,
            size: 23,
          );
        }),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith((states) {
            return states.contains(WidgetState.selected)
                ? AppColors.charcoal
                : AppColors.surface;
          }),
          foregroundColor: WidgetStateProperty.resolveWith((states) {
            return states.contains(WidgetState.selected)
                ? Colors.white
                : AppColors.muted;
          }),
          side: WidgetStateProperty.all(
            const BorderSide(color: AppColors.line),
          ),
          textStyle: WidgetStateProperty.all(
            const TextStyle(fontSize: 12, fontWeight: FontWeight.w800),
          ),
          visualDensity: VisualDensity.compact,
        ),
      ),
    );
  }
}
