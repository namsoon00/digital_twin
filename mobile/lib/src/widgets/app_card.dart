import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

class AppCard extends StatelessWidget {
  const AppCard({
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.onTap,
    super.key,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final content = DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.line),
      ),
      child: Padding(padding: padding, child: child),
    );

    if (onTap == null) {
      return content;
    }

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: content,
      ),
    );
  }
}

class SectionHeader extends StatelessWidget {
  const SectionHeader({
    required this.title,
    this.trailing,
    this.onTap,
    this.tooltip,
    super.key,
  });

  final String title;
  final Widget? trailing;
  final VoidCallback? onTap;
  final String? tooltip;

  @override
  Widget build(BuildContext context) {
    final content = Row(
      children: [
        Expanded(
          child: Text(title, style: Theme.of(context).textTheme.titleLarge),
        ),
        ?trailing,
      ],
    );

    if (onTap == null) {
      return content;
    }

    final tappable = Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: content,
        ),
      ),
    );

    if (tooltip == null) {
      return tappable;
    }

    return Tooltip(message: tooltip!, child: tappable);
  }
}

class FlowChip extends StatelessWidget {
  const FlowChip({required this.label, this.color, super.key});

  final String label;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final effectiveColor = color ?? AppColors.green;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: effectiveColor.withValues(alpha: 0.07),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: effectiveColor.withValues(alpha: 0.18)),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
        child: Text(
          label,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            color: effectiveColor,
            fontSize: 11,
            fontWeight: FontWeight.w800,
            height: 1,
          ),
        ),
      ),
    );
  }
}

class MetricPill extends StatelessWidget {
  const MetricPill({
    required this.icon,
    required this.label,
    required this.value,
    this.color,
    super.key,
  });

  final IconData icon;
  final String label;
  final String value;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final effectiveColor = color ?? AppColors.green;
    return Expanded(
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: AppColors.line),
        ),
        child: Padding(
          padding: const EdgeInsets.all(11),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: effectiveColor, size: 17),
              const SizedBox(height: 10),
              Text(
                value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 4),
              Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
