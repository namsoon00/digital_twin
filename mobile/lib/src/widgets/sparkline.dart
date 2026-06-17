import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

class Sparkline extends StatelessWidget {
  const Sparkline({
    required this.values,
    this.color,
    this.fill = true,
    super.key,
  });

  final List<double> values;
  final Color? color;
  final bool fill;

  @override
  Widget build(BuildContext context) {
    final effectiveColor = color ?? AppColors.green;
    return CustomPaint(
      painter: SparklinePainter(
        values: values,
        color: effectiveColor,
        fill: fill,
      ),
      size: const Size(double.infinity, 64),
    );
  }
}

class SparklinePainter extends CustomPainter {
  const SparklinePainter({
    required this.values,
    required this.color,
    required this.fill,
  });

  final List<double> values;
  final Color color;
  final bool fill;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2 || size.width <= 0 || size.height <= 0) {
      return;
    }

    final minValue = values.reduce((a, b) => a < b ? a : b);
    final maxValue = values.reduce((a, b) => a > b ? a : b);
    final spread = (maxValue - minValue).abs() < 0.001
        ? 1
        : maxValue - minValue;
    final step = size.width / (values.length - 1);
    final points = <Offset>[];

    for (var i = 0; i < values.length; i++) {
      final normalized = (values[i] - minValue) / spread;
      final x = i * step;
      final y =
          size.height - (normalized * size.height * 0.82) - size.height * 0.08;
      points.add(Offset(x, y));
    }

    final gridPaint = Paint()
      ..color = AppColors.line.withValues(alpha: 0.75)
      ..strokeWidth = 1;
    canvas.drawLine(
      Offset(0, size.height * 0.72),
      Offset(size.width, size.height * 0.72),
      gridPaint,
    );

    final path = Path()..moveTo(points.first.dx, points.first.dy);
    for (final point in points.skip(1)) {
      path.lineTo(point.dx, point.dy);
    }

    if (fill) {
      final fillPath = Path.from(path)
        ..lineTo(size.width, size.height)
        ..lineTo(0, size.height)
        ..close();
      final fillPaint = Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [color.withValues(alpha: 0.2), color.withValues(alpha: 0)],
        ).createShader(Offset.zero & size);
      canvas.drawPath(fillPath, fillPaint);
    }

    final linePaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.6
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    canvas.drawPath(path, linePaint);

    final dotPaint = Paint()..color = color;
    canvas.drawCircle(points.last, 4, dotPaint);
    canvas.drawCircle(
      points.last,
      6,
      Paint()..color = color.withValues(alpha: 0.16),
    );
  }

  @override
  bool shouldRepaint(covariant SparklinePainter oldDelegate) {
    return oldDelegate.values != values ||
        oldDelegate.color != color ||
        oldDelegate.fill != fill;
  }
}

class FactorBar extends StatelessWidget {
  const FactorBar({
    required this.label,
    required this.value,
    this.color,
    super.key,
  });

  final String label;
  final int value;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final effectiveColor = color ?? AppColors.green;
    final normalized = value.clamp(0, 100) / 100;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ),
            Text('$value', style: Theme.of(context).textTheme.labelLarge),
          ],
        ),
        const SizedBox(height: 8),
        ClipRRect(
          borderRadius: BorderRadius.circular(999),
          child: LinearProgressIndicator(
            value: normalized.toDouble(),
            minHeight: 8,
            backgroundColor: AppColors.line,
            valueColor: AlwaysStoppedAnimation<Color>(effectiveColor),
          ),
        ),
      ],
    );
  }
}

Color scoreColor(int score) {
  if (score >= 82) {
    return AppColors.green;
  }
  if (score >= 68) {
    return AppColors.blue;
  }
  if (score >= 50) {
    return AppColors.amber;
  }
  return AppColors.red;
}
