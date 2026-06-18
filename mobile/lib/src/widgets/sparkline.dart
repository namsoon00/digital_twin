import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

class Sparkline extends StatefulWidget {
  const Sparkline({
    required this.values,
    this.color,
    this.fill = true,
    this.minVisiblePoints = 3,
    super.key,
  });

  final List<double> values;
  final Color? color;
  final bool fill;
  final int minVisiblePoints;

  @override
  State<Sparkline> createState() => _SparklineState();
}

class _SparklineState extends State<Sparkline> {
  RangeValues _window = const RangeValues(0, 1);
  RangeValues _gestureStartWindow = const RangeValues(0, 1);
  double _gestureFocalFraction = 0.5;
  double _gesturePanFraction = 0;

  @override
  void didUpdateWidget(covariant Sparkline oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.values.length != widget.values.length) {
      _window = const RangeValues(0, 1);
    } else {
      _window = _normalizeWindow(_window, _minWindowSpan);
    }
  }

  @override
  Widget build(BuildContext context) {
    final effectiveColor = widget.color ?? AppColors.green;
    final visibleValues = _visibleValues(widget.values, _window);
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth <= 0 ? 1.0 : constraints.maxWidth;
        return GestureDetector(
          behavior: HitTestBehavior.opaque,
          onDoubleTap: _resetWindow,
          onScaleStart: widget.values.length <= widget.minVisiblePoints
              ? null
              : (details) {
                  _gestureStartWindow = _window;
                  _gesturePanFraction = 0;
                  _gestureFocalFraction = (details.localFocalPoint.dx / width)
                      .clamp(0.0, 1.0)
                      .toDouble();
                },
          onScaleUpdate: widget.values.length <= widget.minVisiblePoints
              ? null
              : (details) {
                  final startSpan =
                      _gestureStartWindow.end - _gestureStartWindow.start;
                  _gesturePanFraction +=
                      -details.focalPointDelta.dx / width * startSpan;
                  setState(() {
                    final targetWindow = _scaledWindow(
                      startWindow: _gestureStartWindow,
                      scale: details.scale,
                      focalFraction: _gestureFocalFraction,
                      panFraction: _gesturePanFraction,
                      minSpan: _minWindowSpan,
                    );
                    _window = _interpolateWindow(_window, targetWindow, 0.72);
                  });
                },
          child: CustomPaint(
            painter: SparklinePainter(
              values: visibleValues,
              color: effectiveColor,
              fill: widget.fill,
            ),
            size: const Size(double.infinity, 64),
          ),
        );
      },
    );
  }

  double get _minWindowSpan {
    if (widget.values.isEmpty) {
      return 1;
    }
    return ((widget.minVisiblePoints.clamp(2, widget.values.length) /
                widget.values.length)
            .clamp(0.0, 1.0))
        .toDouble();
  }

  void _resetWindow() {
    if (_window.start == 0 && _window.end == 1) {
      return;
    }
    setState(() => _window = const RangeValues(0, 1));
  }

  static List<double> _visibleValues(List<double> values, RangeValues window) {
    if (values.length <= 2) {
      return values;
    }
    final maxStart = values.length - 2;
    final start = (window.start * maxStart).floor().clamp(0, maxStart).toInt();
    final end = (window.end * (values.length - 1))
        .ceil()
        .clamp(start + 1, values.length - 1)
        .toInt();
    return values.sublist(start, end + 1);
  }

  static RangeValues _scaledWindow({
    required RangeValues startWindow,
    required double scale,
    required double focalFraction,
    required double panFraction,
    required double minSpan,
  }) {
    final startSpan = startWindow.end - startWindow.start;
    final nextSpan = (startSpan / scale.clamp(0.35, 4.0))
        .clamp(minSpan, 1.0)
        .toDouble();
    final anchor = startWindow.start + startSpan * focalFraction;
    final nextStart = anchor - nextSpan * focalFraction + panFraction;
    return _normalizeWindow(
      RangeValues(nextStart, nextStart + nextSpan),
      minSpan,
    );
  }

  static RangeValues _normalizeWindow(RangeValues window, double minSpan) {
    final span = (window.end - window.start).clamp(minSpan, 1.0).toDouble();
    if (span >= 1) {
      return const RangeValues(0, 1);
    }
    final start = window.start.clamp(0.0, 1.0 - span).toDouble();
    return RangeValues(start, start + span);
  }

  static RangeValues _interpolateWindow(
    RangeValues current,
    RangeValues target,
    double factor,
  ) {
    final clampedFactor = factor.clamp(0.0, 1.0).toDouble();
    return RangeValues(
      current.start + (target.start - current.start) * clampedFactor,
      current.end + (target.end - current.end) * clampedFactor,
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
    final chart = Rect.fromLTWH(
      4,
      6,
      (size.width - 8).clamp(1.0, double.infinity).toDouble(),
      (size.height - 12).clamp(1.0, double.infinity).toDouble(),
    );
    final step = chart.width / (values.length - 1);
    final points = <Offset>[];

    for (var i = 0; i < values.length; i++) {
      final normalized = (values[i] - minValue) / spread;
      final x = chart.left + i * step;
      final y = chart.bottom - normalized * chart.height;
      points.add(Offset(x, y));
    }

    final gridPaint = Paint()
      ..color = AppColors.line.withValues(alpha: 0.48)
      ..strokeWidth = 1;
    for (final fraction in const [0.25, 0.5, 0.75]) {
      final y = chart.top + chart.height * fraction;
      canvas.drawLine(Offset(chart.left, y), Offset(chart.right, y), gridPaint);
    }

    final path = _smoothPath(points);

    if (fill) {
      final fillPath = Path.from(path)
        ..lineTo(chart.right, chart.bottom)
        ..lineTo(chart.left, chart.bottom)
        ..close();
      final fillPaint = Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            color.withValues(alpha: 0.2),
            color.withValues(alpha: 0.04),
            color.withValues(alpha: 0),
          ],
          stops: const [0, 0.52, 1],
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

    canvas.drawCircle(
      points.last,
      7,
      Paint()..color = color.withValues(alpha: 0.18),
    );
    final dotPaint = Paint()..color = color;
    canvas.drawCircle(points.last, 4, dotPaint);
  }

  Path _smoothPath(List<Offset> points) {
    final path = Path()..moveTo(points.first.dx, points.first.dy);
    if (points.length == 2) {
      path.lineTo(points.last.dx, points.last.dy);
      return path;
    }
    for (var i = 1; i < points.length - 1; i++) {
      final current = points[i];
      final next = points[i + 1];
      final midpoint = Offset(
        (current.dx + next.dx) / 2,
        (current.dy + next.dy) / 2,
      );
      path.quadraticBezierTo(current.dx, current.dy, midpoint.dx, midpoint.dy);
    }
    path.lineTo(points.last.dx, points.last.dy);
    return path;
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
